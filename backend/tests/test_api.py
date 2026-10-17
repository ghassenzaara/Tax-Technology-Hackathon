"""Step 0 HTTP API through an in-process ASGI client (no network, fake LLM). Memory
repository for the flow and the rules; Postgres (skipped without TEST_DATABASE_URL) for the
full flow with the real schema, updates, the audit log and the profile read."""

import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest
from pydantic import SecretStr

from app.api.app import create_app
from app.config import PROFILE_SCHEMA
from app.db.memory import MemoryRepository
from app.models.project import ProjectState
from app.profile.schema import load_profile_schema
from tests.helpers import SENTENCE, FakeClient, answer, entry, make_pdf
from tests.profil_data import QUOTE, all_profile_text
from tests.test_cli import MOCK_FILES, fill_template, profil_answer

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
LOOP = "app.api.__main__:selector_loop_factory"
EMBED_SENTENCE = "Die Gesellschaft hält eine ausländische Tochtergesellschaft."


def agent(kwargs):
    """Fake LLM: OCR text, a sentence for step 9, or an extraction answer. The two PDFs
    disagree on the head count, so the draft has one conflict."""
    if "response_format" not in kwargs:
        content = kwargs["messages"][-1]["content"]
        return SENTENCE if isinstance(content, list) else EMBED_SENTENCE
    header = kwargs["messages"][-1]["content"].split("\n")[0]
    employees = 30 if "second" in header else 25
    return answer(
        company_name=entry("Muster GmbH", evidence="Muster GmbH"),
        employees=entry(employees, evidence="Mitarbeitern"),
    )


@asynccontextmanager
async def api(settings, schema, client=None, repository=None):
    """The app with its lifespan, and an HTTP client talking to it in-process."""
    if repository is None and settings.repository == "memory":
        repository = MemoryRepository()
    app = create_app(
        settings,
        repository=repository,  # None: the app builds it from the settings (Postgres)
        schema=schema,
        client=client or FakeClient(responder=agent),
    )
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as http:
            http.services = app.state.services
            yield http


def pdf_bytes(tmp_path, name: str, text: str = SENTENCE) -> bytes:
    return make_pdf(tmp_path / "src" / name, [text]).read_bytes()


def room(tmp_path) -> list[tuple[str, bytes]]:
    return [
        ("DD/first.pdf", pdf_bytes(tmp_path, "first.pdf")),
        ("DD/Steuern/second.pdf", pdf_bytes(tmp_path, "second.pdf")),
        ("DD/notes.txt", b"not supported"),
    ]


async def upload(http, files, project_name=None, use_paths=True):
    parts = [
        ("files", (name.split("/")[-1], data, "application/octet-stream")) for name, data in files
    ]
    data = {"paths": [name for name, _ in files]} if use_paths else {}
    if project_name:
        data["project_name"] = project_name
    return await http.post("/api/uploads", files=parts, data=data)


async def finished(http, company_id) -> dict:
    await http.services.runs.join()
    response = await http.get(f"/api/companies/{company_id}/progress")
    assert response.status_code == 200
    return response.json()


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code and body["message"]
    assert "Traceback" not in response.text
    return body


async def test_health_and_headers(settings, schema):
    async with api(settings, schema) as http:
        response = await http.get("/api/health")
    assert response.json() == {"status": "ok", "database": "ok"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]


async def test_full_flow_memory(settings, schema, tmp_path):
    """Upload, poll, draft, confirm with decisions, poll until cleaned, upload folder gone."""
    async with api(settings, schema) as http:
        response = await upload(http, room(tmp_path), project_name="Projekt Weser")
        assert response.status_code == 202, response.text
        body = response.json()
        company_id = body["company_id"]
        assert company_id == "Projekt-Weser" and body["run_id"]
        assert [a["relative_path"] for a in body["accepted"]] == [
            "DD/first.pdf",
            "DD/Steuern/second.pdf",
            "DD/notes.txt",
        ]
        folder = settings.data_dir / "uploads" / company_id
        assert (folder / "DD" / "Steuern" / "second.pdf").is_file()

        progress = await finished(http, company_id)
        assert progress["run"]["state"] == "finished" and progress["run"]["published"]
        assert progress["project"]["state"] == "published"
        docs = {d["relative_path"]: d for d in progress["documents"]}
        assert docs["DD/notes.txt"]["parse_status"] == "unsupported"
        assert docs["DD/notes.txt"]["extraction_status"] == "skipped"
        assert docs["DD/first.pdf"]["extraction_status"] == "ok"

        draft = (await http.get(f"/api/companies/{company_id}/draft")).json()
        employees = next(f for f in draft["draft"]["fields"] if f["name"] == "employees")
        assert employees["status"] == "conflict" and len(employees["groups"]) == 2
        assert employees["groups"][0]["candidates"][0]["evidence"]  # quotes during review
        decisions = []
        for o in draft["open_decisions"]:
            if o["kind"] == "conflict":
                decisions.append({"field": o["field"], "action": "choose", "group": 1})
            else:
                decisions.append({"field": o["field"], "action": "leave_empty"})

        confirm = await http.post(
            f"/api/companies/{company_id}/confirm",
            json={"draft_version": draft["draft_version"], "decisions": decisions},
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["conflicts_resolved"] == 1
        assert confirm.json()["confirmed_by"] == settings.advisor_label

        progress = await finished(http, company_id)
        assert progress["project"]["state"] == "cleaned"
        assert progress["documents"] == []  # step 10 deleted the per-file progress
        assert not folder.exists()
        repository = http.services.repository
        assert not any((await repository.count_working_rows(company_id)).values())
        assert (await repository.list_runs(company_id))[0].folder is None

        companies = (await http.get("/api/companies")).json()["companies"]
        assert companies == [
            companies[0] | {"company_id": company_id, "project_name": "Projekt Weser"}
        ]
        assert companies[0]["state"] == "cleaned"
        audit = (await http.get("/api/audit", params={"company_id": company_id})).json()
        assert [e["kind"] for e in audit["entries"]] == ["profile_confirmed"]
        assert_error(await http.get(f"/api/companies/{company_id}/draft"), 409, "wrong_state")
        assert_error(await http.post(f"/api/companies/{company_id}/rerun"), 409, "wrong_state")
        # The memory repository cannot read profil.
        assert_error(await http.get(f"/api/companies/{company_id}/profile"), 501, "not_supported")


async def test_upload_rejects_unsafe_paths_and_keeps_the_rest(settings, schema, tmp_path):
    files = [
        ("good.pdf", pdf_bytes(tmp_path, "good.pdf")),
        ("../evil.pdf", b"%PDF evil"),
        ("..\\..\\evil2.pdf", b"%PDF evil"),
        ("C:\\Windows\\evil3.pdf", b"%PDF evil"),
        ("/abs/evil4.pdf", b"%PDF evil"),
        ("x.pdf:stream", b"%PDF evil"),
        ("bad\x01name.pdf", b"%PDF evil"),
        ("good.pdf", b"%PDF duplicate"),
    ]
    async with api(settings, schema) as http:
        response = await upload(http, files)
        await http.services.runs.join()

    assert response.status_code == 202, response.text
    body = response.json()
    assert [a["relative_path"] for a in body["accepted"]] == ["good.pdf"]
    assert len(body["rejected"]) == 7 and all(r["reason"] for r in body["rejected"])
    written = [p for p in tmp_path.rglob("*") if p.is_file() and "evil" in p.name]
    assert written == []


async def test_upload_without_paths_uses_part_file_names(settings, schema, tmp_path):
    async with api(settings, schema) as http:
        response = await upload(
            http, [("DD/sub/a.pdf", pdf_bytes(tmp_path, "a.pdf"))], use_paths=False
        )
        await http.services.runs.join()
    assert response.status_code == 202
    assert response.json()["accepted"][0]["relative_path"] == "a.pdf"


async def test_upload_errors(settings, schema, tmp_path):
    async with api(settings, schema) as http:
        empty = await http.post("/api/uploads", data={"project_name": "x"})
        assert_error(empty, 400, "empty_upload")

        all_bad = await upload(http, [("../a.pdf", b"x"), ("/b.pdf", b"y")], "Nichts")
        body = assert_error(all_bad, 400, "empty_upload")
        assert {d["field"] for d in body["details"]} == {"../a.pdf", "/b.pdf"}
        assert not (settings.data_dir / "uploads" / "Nichts").exists()  # id freed again

        mismatch = await http.post(
            "/api/uploads",
            files=[("files", ("a.pdf", b"x", "application/pdf"))],
            data={"paths": ["a.pdf", "b.pdf"]},
        )
        assert_error(mismatch, 422, "validation")

    small = settings.model_copy(update={"upload_max_total_mb": 0.0001})
    async with api(small, schema) as http:
        big = await upload(http, [("a.pdf", b"x" * 20 * 1024 * 1024)])
    assert_error(big, 413, "upload_too_large")


async def test_oversize_file_rejected_alone(settings, schema, tmp_path):
    settings = settings.model_copy(update={"max_file_mb": 0.01})
    async with api(settings, schema) as http:
        response = await upload(
            http, [("big.pdf", b"%PDF" + b"x" * 20_000), ("ok.pdf", pdf_bytes(tmp_path, "ok.pdf"))]
        )
        await http.services.runs.join()
    body = response.json()
    assert [a["relative_path"] for a in body["accepted"]] == ["ok.pdf"]
    assert body["rejected"][0]["reason"].startswith("file too large")


async def test_state_gating_and_review_errors(settings, schema, tmp_path):
    slow = FakeClient(responder=agent, delay=0.5)
    async with api(settings, schema, client=slow) as http:
        assert_error(await http.get("/api/companies/nope/progress"), 404, "not_found")
        assert_error(await http.get("/api/companies/nope/draft"), 404, "not_found")

        company_id = (await upload(http, room(tmp_path))).json()["company_id"]
        progress = (await http.get(f"/api/companies/{company_id}/progress")).json()
        assert progress["run"]["state"] in ("queued", "running")
        assert {d["extraction_status"] for d in progress["documents"]} == {"pending"}
        assert_error(await http.get(f"/api/companies/{company_id}/draft"), 409, "run_active")
        assert_error(await http.post(f"/api/companies/{company_id}/rerun"), 409, "run_active")
        assert_error(await http.get(f"/api/companies/{company_id}/profile"), 409, "wrong_state")

        await finished(http, company_id)
        draft = (await http.get(f"/api/companies/{company_id}/draft")).json()
        url = f"/api/companies/{company_id}/confirm"

        stale = await http.post(url, json={"draft_version": "old", "decisions": []})
        assert_error(stale, 409, "stale_draft")
        missing = await http.post(url, json={"draft_version": draft["draft_version"]})
        body = assert_error(missing, 422, "validation")
        assert {d["field"] for d in body["details"]} >= {"employees"}
        assert all("decision missing" in d["message"] for d in body["details"])
        bad_body = await http.post(url, json={"decisions": "nope"})
        body = assert_error(bad_body, 422, "validation")
        assert {d["field"] for d in body["details"]} == {"draft_version", "decisions"}
        state = await http.services.repository.get_project_state(company_id)
        assert state.state is ProjectState.PUBLISHED  # nothing changed

        rerun = await http.post(f"/api/companies/{company_id}/rerun")
        assert rerun.status_code == 202 and rerun.json()["started_by"] == "rerun"
        await finished(http, company_id)
        assert (await http.get(f"/api/companies/{company_id}/draft")).status_code == 200


async def test_internal_errors_hide_details(settings, schema):
    repository = MemoryRepository()

    async def broken(*args):
        raise RuntimeError(f"boom in {settings.data_dir.resolve()} with {QUOTE}")

    repository.list_project_states = broken
    async with api(settings, schema, repository=repository) as http:
        response = await http.get("/api/companies")
    body = assert_error(response, 500, "internal_error")
    assert body["error_id"] and QUOTE not in response.text and "boom" not in response.text
    assert str(settings.data_dir) not in response.text


async def test_unknown_route_and_method(settings, schema):
    async with api(settings, schema) as http:
        assert_error(await http.get("/api/nothing"), 404, "not_found")
        assert_error(await http.delete("/api/companies"), 405, "method_not_allowed")


async def test_openapi_lists_every_call(settings, schema):
    async with api(settings, schema) as http:
        spec = (await http.get("/openapi.json")).json()
    assert {
        "/api/health",
        "/api/uploads",
        "/api/companies",
        "/api/companies/{company_id}/progress",
        "/api/companies/{company_id}/rerun",
        "/api/companies/{company_id}/draft",
        "/api/companies/{company_id}/confirm",
        "/api/companies/{company_id}/profile",
        "/api/companies/{company_id}/profile/update",
        "/api/audit",
    } <= set(spec["paths"])
    assert "ErrorBody" in spec["components"]["schemas"]


async def test_restart_marks_runs_interrupted(settings, schema, tmp_path):
    repository = MemoryRepository()
    slow = FakeClient(responder=agent, delay=5)
    async with api(settings, schema, client=slow, repository=repository) as http:
        company_id = (await upload(http, room(tmp_path))).json()["company_id"]
    # The lifespan ended (server stopped) while the run was active.
    async with api(settings, schema, repository=repository) as http:
        progress = (await http.get(f"/api/companies/{company_id}/progress")).json()
        assert progress["run"]["state"] == "interrupted"
        assert (await http.post(f"/api/companies/{company_id}/rerun")).status_code == 202
        assert (await finished(http, company_id))["run"]["state"] == "finished"


# Postgres: the real schema, updates, audit and the profile read ---------------------------


@pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")
async def test_full_flow_postgres(settings, monkeypatch, tmp_path):
    """Mock PDFs through HTTP: upload, draft, confirm, embed and cleanup in the background,
    profile with citations and no quotes, update with stale and busy checks, audit."""
    profil_schema = load_profile_schema(PROFILE_SCHEMA)
    settings = settings.model_copy(
        update={
            "repository": "postgres",
            "database_url": SecretStr(DATABASE_URL),
            "profile_schema_path": PROFILE_SCHEMA,
        }
    )

    def llm(kwargs):
        if "response_format" not in kwargs:
            return EMBED_SENTENCE
        payload = json.loads(profil_answer(kwargs, profil_schema))
        payload["merkmal"].append(
            {
                "merkmal_code": "B07",
                "wert": "ja",
                "kurztext": "Tochter in den Niederlanden",
                "source_page": 1,
                "evidence": "x",
                "confidence": "high",
            }
        )
        return json.dumps(payload)

    names = ("01_Tax-DD-Report.pdf", "02_SPA-Auszug.pdf", "03_Handelsregisterauszug.pdf")
    files = [(f"Datenraum/{n}", (MOCK_FILES / n).read_bytes()) for n in names]
    project = f"test-api-{uuid.uuid4().hex[:8]}"
    async with api(
        settings, profil_schema, client=FakeClient(responder=llm), repository=None
    ) as http:
        response = await upload(http, files, project_name=project)
        assert response.status_code == 202, response.text
        company_id = response.json()["company_id"]
        assert company_id == project
        folder = Path(settings.data_dir) / "uploads" / company_id
        assert (await finished(http, company_id))["project"]["state"] == "published"

        draft = (await http.get(f"/api/companies/{company_id}/draft")).json()
        template = {
            "decisions": [
                {
                    "field": o["field"],
                    **({"key": o["key"]} if o["key"] is not None else {}),
                    "allowed": o["allowed"],
                    "cells": {c: {"group": None} for c in o["cells"]},
                }
                for o in draft["open_decisions"]
            ]
        }
        decisions = fill_template(template)["decisions"]
        confirm = await http.post(
            f"/api/companies/{company_id}/confirm",
            json={"draft_version": draft["draft_version"], "decisions": decisions},
        )
        assert confirm.status_code == 200, confirm.text
        progress = await finished(http, company_id)
        assert progress["project"]["state"] == "cleaned", progress
        assert not folder.exists()

        profile = (await http.get(f"/api/companies/{company_id}/profile")).json()
        assert profile["profile_version"] == 1
        merkmal = next(
            r for r in profile["lists"]["merkmal"] if r["values"]["merkmal_code"] == "B07"
        )
        assert merkmal["fundstelle"] and merkmal["quelle"]["klasse_code"]
        assert QUOTE not in json.dumps(profile)
        assert all(QUOTE not in text for text in await all_profile_text(DATABASE_URL, company_id))

        url = f"/api/companies/{company_id}/profile/update"
        change = {
            "field": "merkmal",
            "key": {"merkmal_code": "B07"},
            "kind": "edit",
            "cells": {"kurztext": "Tochter in NL verkauft"},
        }
        stale = await http.post(url, json={"profile_version": 7, "changes": [change]})
        assert_error(stale, 409, "stale_profile")
        invalid = await http.post(
            url,
            json={
                "profile_version": 1,
                "changes": [
                    {
                        "field": "merkmal",
                        "key": {"merkmal_code": "B07"},
                        "kind": "edit",
                        "cells": {"wert": "vielleicht"},
                    }
                ],
            },
        )
        body = assert_error(invalid, 422, "validation")
        assert body["details"][0]["field"] == "merkmal"

        updated = await http.post(
            url, json={"profile_version": 1, "reason": "Verkauf", "changes": [change]}
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["profile_version"] == 2
        progress = await finished(http, company_id)  # background refresh done
        assert progress["project"]["embeddings_stale"] is False

        audit = (
            await http.get(
                "/api/audit", params={"company_id": company_id, "kind": "profile_changed"}
            )
        ).json()["entries"]
        assert audit[0]["new_value"] and audit[0]["reason"] == "Verkauf"
        assert audit[0]["acted_by"] == settings.advisor_label

        await http.services.repository.begin_step(company_id, "embed", 3600)
        busy = await http.post(url, json={"profile_version": 2, "changes": [change]})
        assert_error(busy, 409, "busy")
        await http.services.repository.end_step(company_id)


def test_server_uses_a_selector_loop():
    """psycopg async fails on the Windows proactor loop that uvicorn picks by default."""
    import asyncio

    from uvicorn import Config

    config = Config("app.api.app:create_app", factory=True, loop=LOOP)
    loop = config.get_loop_factory()()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()
