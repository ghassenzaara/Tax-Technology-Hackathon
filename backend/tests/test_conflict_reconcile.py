"""Conflict equivalence checks, kept separate from screening reconciliation tests."""

import json

import pytest

from app.agents.orchestrator import Orchestrator
from app.db.memory import MemoryRepository
from app.models.audit import AuditFilter
from app.models.extraction import DocumentExtraction, ExtractionStatus
from app.models.profile import FieldStatus, ProfileDraft
from app.models.review import ConfirmRequest
from app.profile.reconcile import reconcile_conflicts, reconciliation_history
from app.profile.review import draft_version, open_decisions
from app.profile.schema import FieldSpec, ProfileSchema
from app.services.confirm import confirm_profile
from app.ui.mapping import draft_facts
from tests.helpers import FakeClient
from tests.test_merge import cand, merge


def reply(outcome="equivalent", preferred_group=0):
    return json.dumps(
        {
            "outcome": outcome,
            "preferred_group": preferred_group,
            "reason": "Die Angaben beschreiben denselben Sachverhalt.",
        }
    )


def text_schema():
    return ProfileSchema(fields=[FieldSpec(name="location", type="string", description="Sitz")])


async def test_selects_existing_value_with_all_sources_and_retains_originals(settings):
    schema = text_schema()
    draft = merge([cand("location", "Hamburg"), cand("location", "Hamburg, HRB 123", "d2")], schema)
    client = FakeClient(text=reply(preferred_group=1))
    result = await reconcile_conflicts(draft, schema, settings, client)
    field = result.field("location")
    assert field.status is FieldStatus.CONFIRMED
    assert field.value == "Hamburg, HRB 123"
    assert field.groups[0].documents == ["d1", "d2"]
    assert field.reconciliation.original_groups == draft.field("location").groups
    assert draft.field("location").status is FieldStatus.CONFLICT
    assert client.completions.calls == 1
    assert open_decisions(result, schema, 1000) == []
    shown = draft_facts(result, schema, {})[0]
    assert shown.reasoning.startswith("KI-Abgleich:")
    assert len(shown.alternatives) == 2
    assert len(shown.sources) == 2
    payload = json.loads(client.completions.requests[0]["messages"][1]["content"])
    assert len(payload["alternatives"]) == 2
    assert payload["alternatives"][0]["sources"][0]["passage"]
    assert ProfileDraft.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "response",
    [reply("unresolved"), reply(preferred_group=99), "invalid JSON", RuntimeError("offline")],
)
async def test_uncertain_invalid_or_failed_answers_leave_conflict(settings, response):
    schema = text_schema()
    draft = merge([cand("location", "Hamburg"), cand("location", "Bremen", "d2")], schema)
    client = FakeClient(responder=lambda _: response)
    result = await reconcile_conflicts(draft, schema, settings, client)
    assert result == draft


async def test_numeric_conflicts_reach_model_and_keep_only_the_selected_sources(schema, settings):
    draft = merge([cand("employees", 25), cand("employees", 40, "d2")], schema)
    client = FakeClient(text=reply("preferred", preferred_group=1))
    result = await reconcile_conflicts(draft, schema, settings, client)
    field = result.field("employees")
    assert field.value == 40
    assert field.groups[0].documents == ["d2"]
    assert field.status is FieldStatus.SINGLE_SOURCE
    assert field.reconciliation.mode == "preferred"
    assert client.completions.calls == 1


def record_schema():
    return ProfileSchema(
        fields=[
            FieldSpec(
                name="holdings",
                type="list",
                key=["owner"],
                description="Gesellschafter",
                items=[
                    FieldSpec(name="owner", type="string", description="Gesellschafter"),
                    FieldSpec(name="address", type="string", description="Sitz"),
                    FieldSpec(name="quote", type="number", description="Quote"),
                    FieldSpec(name="note", type="string", description="Hinweis"),
                ],
            )
        ]
    )


async def test_list_wording_merges_complementary_cells_without_changing_identity(settings):
    schema = record_schema()
    candidates = [
        cand("holdings", {"owner": "A GmbH", "address": "Hamburg", "quote": 40}),
        cand(
            "holdings",
            {"owner": "A GmbH", "address": "Hamburg, HRB 123", "quote": 40, "note": "Direkt"},
            "d2",
        ),
    ]
    draft = merge(candidates, schema)
    result = await reconcile_conflicts(draft, schema, settings, FakeClient(text=reply()))
    field = result.field("holdings")
    assert field.status is FieldStatus.CONFIRMED
    item = field.items[0]
    assert item.key == {"owner": "A GmbH"}
    assert item.value["note"] == "Direkt"
    assert item.value["quote"] == 40
    assert item.reconciliation is not None
    assert draft_facts(result, schema, {})[0].reasoning.startswith("KI-Abgleich:")
    # A differing percentage reaches the model, but cannot combine contradictory rows.
    candidates[1].value["quote"] = 60
    numeric = merge(candidates, schema)
    client = FakeClient(text=reply())
    result = await reconcile_conflicts(numeric, schema, settings, client)
    item = result.field("holdings").items[0]
    selected = numeric.field("holdings").items[0].groups[0]
    assert item.value == selected.value
    assert item.groups[0].documents == selected.documents
    assert item.reconciliation.mode == "preferred"
    assert client.completions.calls == 1


async def test_no_client_no_conflicts_and_source_quality_flags(settings):
    schema = text_schema()
    draft = merge(
        [cand("location", "Hamburg"), cand("location", "Hamburg HRB 123", "d2", needs_review=True)],
        schema,
    )
    assert await reconcile_conflicts(draft, schema, settings, None) == draft
    client = FakeClient(text=reply())
    result = await reconcile_conflicts(draft, schema, settings, client)
    assert result.field("location").needs_review
    await reconcile_conflicts(result, schema, settings, client)
    assert client.completions.calls == 1  # already reconciled: no new call


async def test_preferred_status_uses_context_without_filling_from_rejected_record(settings):
    schema = record_schema()
    schema.field("holdings").items.append(
        FieldSpec(
            name="status",
            type="enum",
            enum=["ongoing", "ended"],
            description="Status",
        )
    )
    old = {"owner": "A GmbH", "address": "Hamburg", "status": "ongoing", "note": "Alter Stand"}
    new = {"owner": "A GmbH", "address": "Hamburg", "status": "ended"}
    draft = merge([cand("holdings", old), cand("holdings", new, "d2")], schema)
    item = draft.field("holdings").items[0]
    preferred = next(i for i, group in enumerate(item.groups) if group.value["status"] == "ended")
    client = FakeClient(text=reply("preferred", preferred))
    extractions = [
        DocumentExtraction(
            company_id="c1",
            doc_id="d2",
            filename="d2.pdf",
            status=ExtractionStatus.OK,
            document_date="2026-10-18",
            document_class="bescheid",
        )
    ]
    result = await reconcile_conflicts(draft, schema, settings, client, extractions)
    selected = result.field("holdings").items[0]
    assert selected.value == new
    assert selected.groups[0].documents == ["d2"]
    assert len(selected.reconciliation.original_groups) == 2
    payload = json.loads(client.completions.requests[0]["messages"][1]["content"])
    sources = [source for group in payload["alternatives"] for source in group["sources"]]
    assert any(
        source["document_date"] == "2026-10-18" and source["document_class"] == "bescheid"
        for source in sources
    )


async def test_pipeline_persists_reconciliation_and_confirmation_audits_it(settings):
    schema = text_schema()
    repo = MemoryRepository(settings)
    for doc, value in [("d1", "Hamburg"), ("d2", "Hamburg HRB 123")]:
        await repo.replace_document_candidates(
            DocumentExtraction(
                company_id="c1",
                doc_id=doc,
                filename=f"{doc}.pdf",
                status=ExtractionStatus.OK,
                candidates=[cand("location", value, doc)],
            )
        )
    pipeline = Orchestrator(schema, repo, settings, FakeClient(text=reply()))
    draft = await pipeline.merge("c1")
    assert (await repo.get_profile_draft("c1")) == draft
    await pipeline.publish(draft, await repo.list_extractions("c1"))
    await confirm_profile(
        repo,
        schema,
        settings,
        ConfirmRequest(
            company_id="c1",
            confirmed_by="Berater",
            draft_version=draft_version(draft),
            decisions=[],
        ),
    )
    entries = await repo.list_audit(AuditFilter(company_id="c1"))
    history = entries[0].new_value["reconciliations"]
    assert history == reconciliation_history(draft)
    assert len(history) == 1
    assert "evidence" not in json.dumps(history)
    assert "sources" not in history[0]
