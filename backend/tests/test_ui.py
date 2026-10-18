"""Klaris through HTTP, with fake model/mail calls and isolated test companies."""

import asyncio
import json
import os
from datetime import date
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from app.db.memory import MemoryRepository
from app.models.job import JobOperation
from app.models.match import Match
from app.models.outreach import DEFAULT_BODY, DEFAULT_SUBJECT
from app.models.project import ProjectState
from app.ui import mapping as m
from app.ui import templates as t
from tests.helpers import FakeClient
from tests.profil_data import QUOTE, with_conflict
from tests.test_api import assert_error
from tests.test_api_workflow import api, model
from tests.test_cli import requires_mock_files
from tests.test_confirm import published
from tests.test_laws import law_pdf
from tests.test_outreach import FakeMail, hit
from tests.test_screen import law, section

CONTRACT = Path(__file__).resolve().parents[2] / "screening" / "contract"
SCHEMAS = {p.name: json.loads(p.read_text("utf-8")) for p in CONTRACT.glob("*.schema.json")}
REGISTRY = Registry().with_resources((k, Resource.from_contents(v)) for k, v in SCHEMAS.items())


def contract(kind, value):
    validator = Draft202012Validator(
        SCHEMAS[f"{kind}.schema.json"], registry=REGISTRY, format_checker=FormatChecker()
    )
    validator.validate(value)


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json() if status != 204 else None


def resolve(draft):
    return {
        f["id"]: (
            {
                "kind": "value",
                "value": f["candidates"][0]["value"],
                "candidateId": f["candidates"][0]["id"],
            }
            if f.get("candidates")
            else {"kind": "left_empty"}
            if f.get("value") is None
            else {"kind": "value", "value": f["value"]}
        )
        for f in draft["facts"]
    }


@pytest.fixture(params=["memory", "postgres"])
async def ui_repo(request, settings, pg_repository_factory):
    if request.param == "postgres":
        return await pg_repository_factory()
    return MemoryRepository(settings)


@pytest.fixture
async def pg_repository_factory():
    repos = []

    async def factory():
        if not os.environ.get("TEST_DATABASE_URL"):
            pytest.skip("TEST_DATABASE_URL not set")
        from app.config import PROFILE_DB_DIR
        from app.db.postgres import PostgresRepository

        repo = PostgresRepository(os.environ["TEST_DATABASE_URL"])
        await repo.initialize()
        await repo.initialize_profile_schema(PROFILE_DB_DIR)
        repos.append(repo)
        return repo

    yield factory
    for repo in repos:
        await repo.close()


async def test_ui_review_confirm_notes_templates_discard(settings, profil_schema, project, ui_repo):
    async with api(settings, profil_schema, repository=ui_repo) as http:
        await published(ui_repo, project, profil_schema, settings, with_conflict(project))
        root = f"/api/ui/companies/{project}"
        draft = ok(await http.get(root + "/draft"))
        assert QUOTE in json.dumps(draft)
        assert any(f["id"].startswith("row:") for f in draft["facts"])
        assert any(f["reviewStatus"] == "conflict" and f["value"] is None for f in draft["facts"])
        assert_error(
            await http.post(
                root + "/confirm", json={"draftVersion": draft["draftVersion"], "resolutions": {}}
            ),
            422,
            "unresolved_fields",
        )
        assert_error(
            await http.post(
                root + "/confirm", json={"draftVersion": "old", "resolutions": resolve(draft)}
            ),
            409,
            "stale_draft",
        )
        client = ok(
            await http.post(
                root + "/confirm",
                json={"draftVersion": draft["draftVersion"], "resolutions": resolve(draft)},
            )
        )
        assert client["status"] == "active"
        assert QUOTE not in json.dumps(client)
        assert all("candidates" not in f for f in client["facts"])
        await http.services.join()
        client = ok(await http.get(root))
        assert_error(await http.get(root + "/draft"), 409, "wrong_state")
        assert client["name"] == f"Testfirma {project} GmbH"
        contract("company", client)
        contract("profile_draft", draft)
        contract("advisor", ok(await http.get("/api/ui/advisor")))
        assert ok(await http.get("/api/ui/companies"))
        assert_error(await http.delete(root), 409, "not_allowed")
        noted = ok(await http.post(root + "/notes", json={"notes": "Prüfen im Gespräch"}))
        assert noted["notes"] == "Prüfen im Gespräch"
        template = ok(await http.get(root + "/email-template"))
        assert "{Anrede}" not in template["body"] and "{Signatur}" not in template["body"]
        assert_error(
            await http.post(
                "/api/ui/email-template", json={"subject": "§ 8c KStG", "body": DEFAULT_BODY}
            ),
            422,
            "law_terms_found",
        )
        ok(
            await http.post(
                "/api/ui/email-template", json={"subject": "Unser Gespräch", "body": DEFAULT_BODY}
            )
        )
        assert not ok(await http.get("/api/ui/email-template"))["isDefault"]
        assert ok(await http.post("/api/ui/email-template/reset"))["isDefault"]
        assert ok(await http.get("/api/ui/email-template/history"))[0]["kind"] == "template_reset"
        history = ok(await http.get(root + "/audit"))
        for entry in history:
            contract("audit_entry", entry)
        assert {"profile_confirmed", "note_changed"} <= {e["kind"] for e in history}
        ok(await http.get(root + "/outreach"))
        ok(await http.get(root + "/processing"))
        discarded = project + "-discard"
        await published(ui_repo, discarded, profil_schema, settings)
        ok(await http.delete(f"/api/ui/companies/{discarded}"), 204)
        assert_error(await http.get(f"/api/ui/companies/{discarded}"), 404, "not_found")


async def test_ui_upload_discard_and_errors(settings, schema, tmp_path):
    async with api(settings, schema) as http:
        assert_error(
            await http.post(
                "/api/ui/uploads",
                files=[("files", ("ok.txt", b"text")), ("files", ("bad.txt", b"text"))],
                data={"paths": ["ok.txt", "../bad.txt"]},
            ),
            422,
            "validation_error",
        )
        assert ok(await http.get("/api/ui/companies/drafts")) == []
        response = await http.post("/api/ui/uploads", files=[("files", ("notes.txt", b"text"))])
        run = ok(response, 202)
        contract("upload_run", run)
        await http.services.join()
        assert ok(await http.get(f"/api/ui/runs/{run['id']}"))["status"] == "finished"
        assert any(
            c["id"] == run["companyId"] for c in ok(await http.get("/api/ui/companies/drafts"))
        )
        ok(await http.delete(f"/api/ui/companies/{run['companyId']}"), 204)
        assert_error(await http.get(f"/api/ui/companies/{run['companyId']}"), 404, "not_found")
        assert_error(await http.get("/api/ui/runs/unknown"), 404, "not_found")
        assert_error(await http.get("/api/ui/law-changes/99"), 404, "not_found")


def test_template_parity_and_list_coercion(profil_schema):
    assert t.find_law_terms("§ 8c KStG")
    assert t.find_law_terms("BFH I R 1/23")
    assert t.find_law_terms("Jahressteuergesetz")
    assert not t.find_law_terms(DEFAULT_SUBJECT + DEFAULT_BODY)
    assert t.unknown_placeholders("{Anrede} {Signatur} {Andere}") == ["{Andere}"]
    spec = profil_schema.field("finding").item("normen")
    assert m.coerce(spec, "§ 8c KStG, § 8b KStG") == ["§ 8c KStG", "§ 8b KStG"]


async def test_screening_button_ingests_once_and_reuses_decisions(
    settings, profil_schema, project, tmp_path
):
    source = tmp_path / "sources"
    source.mkdir()
    law_pdf(source)
    settings = settings.model_copy(update={"law_source_dir": source})
    client = FakeClient(responder=model)
    repo = MemoryRepository(settings)
    async with api(settings, profil_schema, repository=repo, client=client) as http:
        await activate(http, repo, project, profil_schema, settings)
        first = ok(await http.post("/api/ui/screening-runs"), 202)
        await http.services.join()
        job = ok(await http.get(f"/api/ui/jobs/{first['id']}"))
        assert job["status"] == "succeeded", job
        assert job["result"]["lawChanges"] == 1
        assert not job["result"]["upToDate"]
        [doc] = job["result"]["documents"]
        assert doc["new"] and doc["filename"].endswith(".pdf")
        assert doc["lawChangeId"] == job["result"]["newLawChangeIds"][0]
        assert job["result"]["companiesScreened"] == 1
        assert job["result"]["hits"] > 0
        assert ok(await http.get("/api/ui/screening-runs/latest"))["id"] == job["id"]
        calls = client.completions.calls
        second = ok(await http.post("/api/ui/screening-runs"), 202)
        await http.services.join()
        job = ok(await http.get(f"/api/ui/jobs/{second['id']}"))
        assert job["status"] == "succeeded", job
        assert job["result"]["lawChanges"] == 0
        assert job["result"]["upToDate"]
        assert [d["new"] for d in job["result"]["documents"]] == [False]
        assert client.completions.calls == calls


async def test_screening_busy_failure_retry_and_recovery(settings, profil_schema, project, ui_repo):
    async with api(settings, profil_schema, repository=ui_repo) as http:
        gate = asyncio.Event()

        async def fail(job):
            await gate.wait()
            raise RuntimeError("scripted failure")

        http.services.jobs._handlers[JobOperation.SCREENING_RUN] = fail
        first = ok(await http.post("/api/ui/screening-runs"), 202)
        assert_error(await http.post("/api/ui/screening-runs"), 409, "busy")
        gate.set()
        await http.services.join()
        failed = ok(await http.get(f"/api/ui/jobs/{first['id']}"))
        assert failed["status"] == "failed" and failed["error"]

        async def succeed(job):
            return {"lawChanges": 0}

        http.services.jobs._handlers[JobOperation.SCREENING_RUN] = succeed
        retried = ok(await http.post(f"/api/ui/jobs/{first['id']}/retry"), 202)
        assert retried["retryOf"] == first["id"]
        await http.services.join()
        assert ok(await http.get(f"/api/ui/jobs/{retried['id']}"))["status"] == "succeeded"
        assert_error(await http.post(f"/api/ui/jobs/{first['id']}/retry"), 409, "wrong_state")
        root = await activate(http, ui_repo, project, profil_schema, settings)
        state = ok(await http.get(root + "/processing"))
        assert not state["embeddingsStale"] and not state.get("activeJob")
        assert ok(await http.post(root + "/recover")) is None


async def test_ui_recover_initial_confirmation_with_retained_draft(
    settings, profil_schema, project, ui_repo
):
    async with api(settings, profil_schema, repository=ui_repo) as http:

        async def interrupted(job):
            raise RuntimeError("embedding interrupted")

        http.services.jobs._handlers[JobOperation.ADVANCE] = interrupted
        root = await activate(http, ui_repo, project, profil_schema, settings)
        state = ok(await http.get(root + "/processing"))
        assert state["state"] == "confirmed" and state["recovery"]["available"]
        assert not state["updatePending"]
        assert await ui_repo.get_profile_draft(project) is not None
        http.services.jobs._handlers[JobOperation.ADVANCE] = http.services.jobs._advance
        job = ok(await http.post(root + "/recover"))
        assert job["operation"] == "advance"
        await http.services.join()
        assert (await ui_repo.get_project_state(project)).state is ProjectState.CLEANED
        assert await ui_repo.get_profile_draft(project) is None
        history = ok(await http.get(root + "/audit"))
        assert sum(e["kind"] == "profile_confirmed" for e in history) == 1


async def activate(http, repo, project, schema, settings):
    await published(repo, project, schema, settings)
    root = f"/api/ui/companies/{project}"
    draft = ok(await http.get(root + "/draft"))
    ok(
        await http.post(
            root + "/confirm",
            json={"draftVersion": draft["draftVersion"], "resolutions": resolve(draft)},
        )
    )
    await http.services.join()
    return root


async def test_ui_multi_send_exact_snapshot_guards_and_resolution(
    settings, profil_schema, project, ui_repo
):
    mail = FakeMail()
    async with api(settings, profil_schema, repository=ui_repo, mail=mail) as http:
        root = await activate(http, ui_repo, project, profil_schema, settings)
        alert = await hit(ui_repo, settings, project)
        law2 = await ui_repo.save_law(
            law().model_copy(update={"content_hash": project + "-2"}),
            [section(settings)],
            replace=False,
        )
        sections = await ui_repo.list_law_sections(law2)
        await ui_repo.save_matches(
            [
                Match(
                    company_id=project,
                    law_id=law2,
                    law_section_id=sections[0].id,
                    objekt_typ="finding",
                    objekt_id=1,
                    feature_text="Verlustvorträge",
                    score=0.8,
                    judge_relevant=True,
                    judge_direction="risiko",
                    judge_reason="Betroffen",
                    model="fake",
                    profile_version=1,
                )
            ]
        )
        matches = ok(await http.get("/api/ui/matches", params={"companyId": project}))
        assert len(matches) == 2
        for match in matches:
            contract("match", match)
            assert match["assessment"]["current"]
            ok(await http.post(f"/api/ui/matches/{match['id']}/review", json={"label": "relevant"}))
        laws = ok(await http.get("/api/ui/law-changes"))
        for source in laws:
            contract("law_change", source)
        ok(await http.get(f"/api/ui/law-changes/{law2}"))
        ok(await http.post(f"/api/ui/law-changes/{law2}/seen"), 204)
        assert str(law2) in ok(await http.get("/api/ui/advisor"))["seenLawChangeIds"]
        body = {
            "companyId": project,
            "matchIds": [x["id"] for x in matches],
            "recipient": "client@example.test",
            "subject": "  Unser Gespräch  ",
            "body": "  Guten Tag,\nbitte melden Sie sich.  ",
            "confirmed": True,
        }
        assert_error(
            await http.post(
                "/api/ui/emails", json={k: v for k, v in body.items() if k != "confirmed"}
            ),
            422,
            "validation",
        )
        assert_error(
            await http.post("/api/ui/emails", json={**body, "body": "§ 8c KStG"}),
            422,
            "law_terms_found",
        )
        sent = ok(await http.post("/api/ui/emails", json=body))
        contract("outreach", sent)
        assert sent["body"] == body["body"] and sent["subject"] == body["subject"]
        assert set(sent["matchIds"]) == set(body["matchIds"])
        assert len(mail.calls) == 1
        assert_error(await http.post("/api/ui/emails", json=body), 409, "already_sent")
        for match in matches:
            assert_error(
                await http.post(
                    f"/api/ui/matches/{match['id']}/review", json={"emailStatus": "not_sent"}
                ),
                409,
                "status_locked",
            )
            ok(
                await http.post(
                    f"/api/ui/matches/{match['id']}/review", json={"emailStatus": "discussed"}
                )
            )
        assert len(ok(await http.get(root + "/outreach"))) == 1
        assert (await ui_repo.get_alert(alert.id)).status == "actioned"

        # Calendar: the sent email waits for a reply recorded by hand.
        [entry] = [x for x in ok(await http.get("/api/ui/calendar")) if x["companyId"] == project]
        assert entry["outreachId"] == sent["id"] and entry["status"] == "awaiting"
        assert "response" not in entry
        reply = f"/api/ui/outreach/{sent['id']}/response"
        today = date.today().isoformat()
        assert_error(
            await http.post(reply, json={"outcome": "accepted", "receivedOn": today}),
            422,
            "validation_error",
        )
        assert_error(
            await http.post(reply, json={"outcome": "rejected", "receivedOn": "2000-01-01"}),
            422,
            "validation_error",
        )
        assert_error(
            await http.post(
                "/api/ui/outreach/999999/response",
                json={"outcome": "rejected", "receivedOn": today},
            ),
            404,
            "not_found",
        )
        meeting = "2030-05-06T10:30:00+02:00"
        entry = ok(
            await http.post(
                reply,
                json={"outcome": "accepted", "receivedOn": today, "meetingAt": meeting},
            )
        )
        assert entry["status"] == "accepted"
        assert entry["response"]["meetingAt"] == "2030-05-06T08:30:00Z"
        entry = ok(
            await http.post(
                reply,
                json={"outcome": "rejected", "receivedOn": today, "meetingAt": meeting},
            )
        )
        assert entry["status"] == "rejected" and "meetingAt" not in entry["response"]
        history = ok(await http.get(root + "/audit"))
        assert [x["summary"] for x in history if x["kind"] == "response_recorded"] == [
            "Antwort erfasst: abgelehnt",
            "Antwort erfasst: Besprechung zugesagt",
        ]

        # Prototype reset: clients, sources, emails and audit go; the template stays. Memory
        # only: on Postgres it would empty the whole TEST_DATABASE_URL database, which the
        # docs allow to be the development database.
        if isinstance(ui_repo, MemoryRepository):
            template = ok(await http.get("/api/ui/email-template"))
            ok(await http.post("/api/ui/reset"), 204)
            assert ok(await http.get("/api/ui/companies")) == []
            assert ok(await http.get("/api/ui/law-changes")) == []
            assert ok(await http.get("/api/ui/calendar")) == []
            assert ok(await http.get("/api/ui/email-template")) == template


@requires_mock_files
async def test_ui_profile_edit_and_document_update(
    settings, profil_schema, project, ui_repo, tmp_path
):
    from tests.helpers import FakeClient
    from tests.test_api import EMBED_SENTENCE
    from tests.test_cli import MOCK_FILES, profil_answer

    def llm(kwargs):
        return (
            profil_answer(kwargs, profil_schema) if "response_format" in kwargs else EMBED_SENTENCE
        )

    async with api(
        settings, profil_schema, repository=ui_repo, client=FakeClient(responder=llm)
    ) as http:
        root = await activate(http, ui_repo, project, profil_schema, settings)
        client = ok(await http.get(root))
        version = client["extra"]["profileVersion"]
        updated = ok(
            await http.post(
                root + "/update",
                json={
                    "profileVersion": version,
                    "core": {
                        "name": "Geänderte Firma GmbH",
                        "contact": {"name": "Maria Beispiel", "email": "maria@example.test"},
                        "customerNumber": "K-12",
                    },
                    "facts": [{"id": "finanzamt", "value": "FA Hamburg"}],
                },
            )
        )
        assert updated["name"] == "Geänderte Firma GmbH" and updated["customerNumber"] == "K-12"
        assert updated["contact"]["email"] == "maria@example.test"
        fact = next(f for f in updated["facts"] if f["id"] == "finanzamt")
        assert fact["reviewStatus"] == "advisor_entered" and fact["sources"] == []
        await http.services.join()
        assert_error(
            await http.post(
                root + "/update", json={"profileVersion": version, "core": {"name": "Alter Stand"}}
            ),
            409,
            "stale_profile",
        )
        assert_error(
            await http.post(
                root + "/update",
                json={
                    "profileVersion": version + 1,
                    "facts": [{"id": "kaufpreis_eur", "value": "abc"}],
                },
            ),
            422,
            "validation_error",
        )
        filename = "03_Handelsregisterauszug.pdf"
        uploaded = ok(
            await http.post(
                "/api/ui/uploads",
                data={"companyId": project},
                files=[("files", (filename, (MOCK_FILES / filename).read_bytes()))],
            ),
            202,
        )
        await http.services.join()
        assert ok(await http.get(f"/api/ui/runs/{uploaded['id']}"))["status"] == "finished"
        assert (await ui_repo.get_project_state(project)).state is ProjectState.CLEANED
        assert ok(await http.get(root))["name"] == "Nordhafen Logistik GmbH (Test)"
        assert_error(await http.get(root + "/draft"), 409, "wrong_state")
        client = ok(await http.get(root))
        assert client["name"] == "Nordhafen Logistik GmbH (Test)"
        assert next(f for f in client["facts"] if f["id"] == "finanzamt")["value"] == "FA Hamburg"
        assert await ui_repo.get_profile_draft(project) is None
        assert not (settings.data_dir / "uploads" / project).exists()
        assert any(e["kind"] == "documents_added" for e in ok(await http.get(root + "/audit")))
        version = client["extra"]["profileVersion"]
        changed = ok(
            await http.post(
                root + "/update",
                json={"profileVersion": version, "core": {"customerNumber": "K-13"}},
            )
        )
        assert changed["extra"]["profileVersion"] == version + 1
        assert_error(
            await http.post(
                root + "/update",
                json={"profileVersion": version, "core": {"customerNumber": "Stale"}},
            ),
            409,
            "stale_profile",
        )


@pytest.mark.parametrize("uncertain", [False, True])
async def test_ui_send_failure_needs_deliberate_resolution(
    settings, profil_schema, project, ui_repo, uncertain
):
    from app.mail.provider import DeliveryUncertain, MailError

    mail = FakeMail(DeliveryUncertain("unknown") if uncertain else MailError("rejected"))
    async with api(settings, profil_schema, repository=ui_repo, mail=mail) as http:
        await activate(http, ui_repo, project, profil_schema, settings)
        alert = await hit(ui_repo, settings, project)
        match_id = m.match_id(alert.law.id, project)
        ok(await http.post(f"/api/ui/matches/{match_id}/review", json={"label": "relevant"}))
        body = {
            "companyId": project,
            "matchIds": [match_id],
            "recipient": "client@example.test",
            "subject": "Gespräch",
            "body": "Bitte melden Sie sich.",
            "confirmed": True,
        }
        failed = await http.post("/api/ui/emails", json=body)
        error = assert_error(
            failed, 409 if uncertain else 502, "send_pending" if uncertain else "send_failed"
        )
        if uncertain:
            assert_error(await http.post("/api/ui/emails", json=body), 409, "send_pending")
            assert_error(
                await http.post(f"/api/ui/matches/{match_id}/review", json={"notes": "Text"}),
                409,
                "send_pending",
            )
            # The legacy send also respects a pending multi-law reservation.
            assert_error(
                await http.post(
                    f"/api/alerts/{alert.id}/send",
                    json={
                        "recipient": body["recipient"],
                        "subject": body["subject"],
                        "body": body["body"],
                        "confirmed": True,
                    },
                ),
                409,
                "send_pending",
            )
            assert len(mail.calls) == 1
            await ui_repo.finish_outreach(error["outreach_id"], "failed")
        mail.error = None
        ok(await http.post("/api/ui/emails", json=body))
        assert len(mail.calls) == 2


async def test_ui_auto_save_selects_conflicts_and_keeps_missing_editable(
    settings, profil_schema, project
):
    from app.models.audit import AuditFilter
    from app.models.run import Run, RunState
    from tests.test_api import EMBED_SENTENCE

    async with api(settings, profil_schema, client=FakeClient(text=EMBED_SENTENCE)) as http:
        repo = http.services.repository
        await published(repo, project, profil_schema, settings, with_conflict(project))
        expected = (await repo.get_profile_draft(project)).field("kaufpreis_eur").chosen
        run = Run(
            run_id="automatic-save",
            company_id=project,
            started_by="ui_upload",
            state=RunState.FINISHED,
            published=True,
        )
        await repo.save_run(run)
        await http.services.runs.on_finished(run)
        await http.services.join()
        root = f"/api/ui/companies/{project}"
        client = ok(await http.get(root))
        assert client["status"] == "active"
        assert next(f["value"] for f in client["facts"] if f["id"] == "kaufpreis_eur") == expected
        assert not any(f.get("needsReview") for f in client["facts"])
        audit = await repo.list_audit(AuditFilter(company_id=project))
        assert audit[0].acted_by == "Automatische Profilerstellung"
        saved = ok(
            await http.post(
                root + "/update",
                json={
                    "profileVersion": client["extra"]["profileVersion"],
                    "core": {"contact": {"email": "later@example.test"}},
                },
            )
        )
        assert saved["contact"]["email"] == "later@example.test"
        assert (
            next(f for f in saved["extra"]["coreFacts"] if f["id"] == "ansprechpartner_email")[
                "reviewStatus"
            ]
            != "missing"
        )


@requires_mock_files
async def test_ui_full_mock_room_upload(settings, profil_schema, ui_repo):
    from tests.test_api import EMBED_SENTENCE
    from tests.test_cli import MOCK_FILES, profil_answer

    def llm(kwargs):
        return (
            profil_answer(kwargs, profil_schema) if "response_format" in kwargs else EMBED_SENTENCE
        )

    async with api(
        settings, profil_schema, repository=ui_repo, client=FakeClient(responder=llm)
    ) as http:
        files = sorted(MOCK_FILES.glob("*.pdf"))
        assert len(files) == 17
        run = ok(
            await http.post(
                "/api/ui/uploads",
                files=[("files", (p.name, p.read_bytes(), "application/pdf")) for p in files],
                data={"paths": [f"Datenraum/{p.name}" for p in files]},
            ),
            202,
        )
        assert len(run["documents"]) == 17
        await http.services.join()
        progress = ok(await http.get(f"/api/ui/runs/{run['id']}"))
        assert progress["status"] == "finished"
        assert sum((await ui_repo.get_run(run["id"])).counts.values()) == 17
        contract("upload_run", progress)
        root = f"/api/ui/companies/{run['companyId']}"
        assert_error(await http.get(root + "/draft"), 409, "wrong_state")
        client = ok(await http.get(root))
        assert client["status"] == "active"
        assert client["name"] == "Nordhafen Logistik GmbH (Test)"
        contract("company", client)
        assert all("quote" not in source for fact in client["facts"] for source in fact["sources"])
        assert (await ui_repo.get_project_state(run["companyId"])).state is ProjectState.CLEANED
        fields = client["facts"] + client["extra"]["coreFacts"]
        assert all(f["sources"] == [] for f in fields)
        assert (await ui_repo.get_client_info(run["companyId"])).citations == {}
        assert all(f["reviewStatus"] in {"confirmed", "missing"} for f in fields)
        assert any(f["reviewStatus"] == "missing" for f in fields)
        assert not any(f.get("needsReview") for f in fields)
        assert ok(await http.get("/api/ui/companies/drafts")) == []
        assert (
            sum(e["kind"] == "profile_confirmed" for e in ok(await http.get(root + "/audit"))) == 1
        )


async def test_ui_finding_norm_list_edits_and_clear(settings, profil_schema, project, ui_repo):
    async with api(settings, profil_schema, repository=ui_repo) as http:
        root = await activate(http, ui_repo, project, profil_schema, settings)
        for text, expected in [("§ 8b KStG", ["§ 8b KStG"]), ("", [])]:
            client = ok(await http.get(root))
            fact = next(f for f in client["facts"] if f["id"] == "list:finding")
            fact["value"][0]["Normen"] = text
            updated = ok(
                await http.post(
                    root + "/update",
                    json={
                        "profileVersion": client["extra"]["profileVersion"],
                        "facts": [{"id": fact["id"], "value": fact["value"]}],
                    },
                )
            )
            await http.services.join()
            profile = await ui_repo.read_ui_profile(project, profil_schema, settings)
            assert profile.lists["finding"][0].values["normen"] == expected
            assert updated["extra"]["profileVersion"] == client["extra"]["profileVersion"] + 1
