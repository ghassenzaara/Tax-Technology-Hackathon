"""Steps 11 to 18 through HTTP, browser-readable errors, jobs and recovery (findings 1, 5 to
9). In-process ASGI client, FakeClient for every model call, a fake or the log mail
transport; the Postgres acceptance flow is skipped without TEST_DATABASE_URL."""

import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import psycopg
import pytest
from pydantic import SecretStr

from app.api.app import create_app
from app.config import PROFILE_SCHEMA
from app.db.memory import MemoryRepository
from app.db.postgres import PostgresRepository
from app.mail.provider import DeliveryUncertain, MailError
from app.models.project import ProjectState
from app.profile.schema import load_profile_schema
from tests.helpers import FakeClient
from tests.profil_data import QUOTE
from tests.test_api import EMBED_SENTENCE, agent, assert_error, finished, room, upload
from tests.test_cli import MOCK_FILES, fill_template, profil_answer
from tests.test_laws import METADATA, law_pdf
from tests.test_outreach import FakeMail
from tests.test_screen import feature, ready, verdict

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
ORIGIN = "http://localhost:3000"


def model(kwargs):
    """Every model call of the workflow: law metadata, judge, sentences, extraction."""
    response_format = kwargs.get("response_format")
    name = response_format["json_schema"]["name"] if response_format else None
    if name == "law_metadata":
        return json.dumps(METADATA)
    if name == "law_relevance":
        return verdict()
    return agent(kwargs)


@asynccontextmanager
async def api(settings, schema, *, client=None, repository=None, mail=None):
    if repository is None and settings.repository == "memory":
        repository = MemoryRepository(settings)
    app = create_app(
        settings,
        repository=repository,
        schema=schema,
        client=FakeClient(responder=model) if client is None else client,
        mail=mail,
    )
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as http:
            http.services = app.state.services
            yield http


async def job_done(http, response) -> dict:
    assert response.status_code == 202, response.text
    assert response.headers["location"] == f"/api/jobs/{response.json()['id']}"
    await http.services.join()
    job = await http.get(response.headers["location"])
    assert job.status_code == 200
    return job.json()


def law_upload(tmp_path, name="gesetz.pdf"):
    return {"file": (name, law_pdf(tmp_path, name).read_bytes(), "application/pdf")}


# Finding 7: browser-readable errors -------------------------------------------------------


def assert_browser_headers(response):
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"


async def test_cors_and_context_headers_on_success_and_errors(settings, schema):
    repository = MemoryRepository(settings)

    async def broken(*args):
        raise RuntimeError(f"boom with {QUOTE}")

    async with api(settings, schema, repository=repository) as http:
        headers = {"Origin": ORIGIN}
        assert_browser_headers(await http.get("/api/health", headers=headers))
        invalid = await http.post("/api/screenings", json={"bogus": 1}, headers=headers)
        assert_error(invalid, 422, "validation")
        assert_browser_headers(invalid)
        missing = await http.get("/api/jobs/nope", headers=headers)
        assert_error(missing, 404, "not_found")
        assert_browser_headers(missing)

        repository.list_project_states = broken
        failed = await http.get("/api/companies", headers=headers)
        body = assert_error(failed, 500, "internal_error")
        assert_browser_headers(failed)
        assert body["error_id"] == failed.headers["x-request-id"] and QUOTE not in failed.text

    small = settings.model_copy(update={"upload_max_total_mb": 0.0001, "max_file_mb": 0.0001})
    async with api(small, schema) as http:
        big = await http.post(
            "/api/uploads",
            files=[("files", ("a.pdf", b"x" * 20 * 1024 * 1024, "application/pdf"))],
            headers={"Origin": ORIGIN},
        )
        assert_error(big, 413, "upload_too_large")
        assert_browser_headers(big)
        big_law = await http.post(
            "/api/laws",
            files={"file": ("big.pdf", b"%PDF-" + b"x" * 200_000, "application/pdf")},
            headers={"Origin": ORIGIN},
        )
        assert_error(big_law, 413, "upload_too_large")
        assert_browser_headers(big_law)


# Finding 1: laws, screening, matches and jobs ------------------------------------------


async def test_law_upload_job_listing_and_detail(settings, schema, tmp_path):
    source = law_upload(tmp_path)  # the same bytes for the duplicate check
    async with api(settings, schema) as http:
        job = await job_done(http, await http.post("/api/laws", files=source))
        assert job["status"] == "succeeded" and job["operation"] == "law_ingest"
        assert "path" not in json.dumps(job) and "private" not in job
        law_id = job["result"]["law_id"]
        assert job["result"]["change_summary"] == METADATA["change_summary"]
        assert not (settings.data_dir / "law_uploads" / job["id"]).exists()  # input removed

        (listed,) = (await http.get("/api/laws")).json()["laws"]
        assert listed["id"] == law_id and listed["sections"] >= 1
        assert listed["filename"] == "gesetz.pdf" and listed["summary"]
        detail = (await http.get(f"/api/laws/{law_id}")).json()
        assert len(detail["sections"]) == listed["sections"] and detail["law"]["id"] == law_id
        assert all(s["text"] and s["model"] for s in detail["sections"])
        assert "vector" not in json.dumps(detail)
        assert_error(await http.get("/api/laws/999"), 404, "not_found")

        duplicate = await http.post("/api/laws", files=source)
        assert_error(duplicate, 409, "duplicate")
        replaced = await job_done(
            http, await http.post("/api/laws", files=source, data={"replace": "true"})
        )
        assert replaced["result"]["replaced"]

        not_pdf = await http.post("/api/laws", files={"file": ("a.pdf", b"hello", "text/plain")})
        assert_error(not_pdf, 422, "validation")
        wrong_name = await http.post("/api/laws", files={"file": ("a.txt", b"%PDF-1.4", "x")})
        assert_error(wrong_name, 422, "validation")
        assert list((settings.data_dir / "law_uploads").iterdir()) == []  # refused: nothing kept

    async with api(settings, schema, client=None, repository=MemoryRepository()) as http:
        http.services.jobs.client = None
        no_key = await http.post("/api/laws", files=law_upload(tmp_path))
        assert_error(no_key, 503, "llm_unavailable")


async def test_law_upload_screens_every_eligible_company(settings, schema, project, tmp_path):
    """A new source alerts without another call; one company's problem never stops the
    others (here its vectors have another dimension)."""
    four = [1.0, 1.0, 1.0, 1.0]
    repository = await ready(
        settings, project, features=[feature(settings, vector=four)], sections=[]
    )
    broken = await ready(settings, "other-company")  # 2-dim feature vectors
    repository.states["other-company"] = broken.states["other-company"]
    repository.embeddings["other-company"] = broken.embeddings["other-company"]
    async with api(settings, schema, repository=repository) as http:
        job = await job_done(http, await http.post("/api/laws", files=law_upload(tmp_path)))
        assert job["status"] == "succeeded", job
        reports = {r["company_id"]: r for r in job["result"]["screening"]}
        assert reports[project]["status"] == "screened" and reports[project]["hits"] >= 1
        assert reports["other-company"]["status"] == "failed"
        assert "dimensions differ" in reports["other-company"]["message"]
        alerts = (await http.get("/api/alerts", params={"company_id": project})).json()
        assert alerts["counts"]["new"] == reports[project]["hits"]
        assert all(a["law"]["id"] == job["result"]["law_id"] for a in alerts["alerts"])

    off = settings.model_copy(update={"screen_after_ingest": False})
    async with api(off, schema, repository=MemoryRepository(off)) as http:
        job = await job_done(http, await http.post("/api/laws", files=law_upload(tmp_path)))
        assert "screening" not in job["result"]


async def test_failed_law_job_keeps_input_for_an_explicit_retry(settings, schema, tmp_path):
    answers = {"metadata": "not json"}

    def flaky(kwargs):
        if kwargs.get("response_format", {}).get("json_schema", {}).get("name") == "law_metadata":
            return answers["metadata"]
        return model(kwargs)

    async with api(settings, schema, client=FakeClient(responder=flaky)) as http:
        failed = await job_done(http, await http.post("/api/laws", files=law_upload(tmp_path)))
        assert failed["status"] == "failed" and failed["error"]
        assert str(settings.data_dir.resolve()) not in failed["error"]
        assert (settings.data_dir / "law_uploads" / failed["id"] / "source.pdf").is_file()

        answers["metadata"] = json.dumps(METADATA)
        retried = await job_done(http, await http.post(f"/api/jobs/{failed['id']}/retry"))
        assert retried["status"] == "succeeded" and retried["retry_of"] == failed["id"]
        assert retried["result"]["title"] == METADATA["title"]
        again = await http.post(f"/api/jobs/{failed['id']}/retry")
        assert_error(again, 409, "wrong_state")
        assert_error(await http.post(f"/api/jobs/{retried['id']}/retry"), 409, "wrong_state")
        assert_error(await http.post("/api/jobs/nope/retry"), 404, "not_found")


async def screened_api(settings, schema, project, mail=None):
    repository = await ready(settings, project)
    return api(settings, schema, repository=repository, mail=mail), repository


async def test_screening_jobs_matches_and_dry_run(settings, schema, project):
    context, repository = await screened_api(settings, schema, project)
    async with context as http:
        dry = await job_done(
            http, await http.post("/api/screenings", json={"company_id": project, "dry_run": True})
        )
        (report,) = dry["result"]["reports"]
        assert report["dry_run"] and report["candidates"] == 1 and "vektor" not in json.dumps(dry)
        assert (await http.get(f"/api/companies/{project}/matches")).json()["matches"] == []

        job = await job_done(http, await http.post("/api/screenings", json={"company_id": project}))
        assert job["status"] == "succeeded" and job["result"]["complete"]
        (match,) = (await http.get(f"/api/companies/{project}/matches")).json()["matches"]
        assert match["is_current"] and match["judge_relevant"] and match["input_fingerprint"]

        progress = (await http.get(f"/api/companies/{project}/progress")).json()
        assert progress["screening"]["pending"] is False
        assert progress["latest_job"]["id"] == job["id"] and progress["active_job"] is None

        repository.embeddings[project] = []
        await job_done(http, await http.post("/api/screenings", json={"company_id": project}))
        assert (await http.get(f"/api/companies/{project}/matches")).json()["matches"] == []
        history = await http.get(
            f"/api/companies/{project}/matches", params={"include_history": "true"}
        )
        (old,) = history.json()["matches"]
        assert old["invalidation_reason"] == "not_candidate" and old["judge_relevant"]

        assert_error(
            await http.post("/api/screenings", json={"company_id": project, "law_id": 999}),
            404,
            "not_found",
        )
        assert_error(
            await http.post("/api/screenings", json={"company_id": "nope"}), 404, "not_found"
        )
        repository.states[project].embeddings_stale = True
        stale = await http.post("/api/screenings", json={"company_id": project})
        assert_error(stale, 409, "wrong_state")
        all_companies = await job_done(http, await http.post("/api/screenings", json={}))
        assert all_companies["result"]["reports"][0]["status"] == "refused"


async def test_alerts_template_preview_send_and_history(settings, schema, project):
    mail = FakeMail()
    context, repository = await screened_api(settings, schema, project, mail=mail)
    async with context as http:
        await job_done(http, await http.post("/api/screenings", json={"company_id": project}))
        listing = (await http.get("/api/alerts", params={"company_id": project})).json()
        (alert,) = listing["alerts"]
        assert listing["counts"]["new"] == 1 and alert["status"] == "new"
        url = f"/api/alerts/{alert['id']}"
        assert (await http.get(url)).json()["status"] == "new"  # reading never marks seen
        assert (await http.post(f"{url}/seen")).json()["status"] == "seen"
        assert_error(await http.post(f"{url}/seen"), 409, "wrong_state")

        template = (await http.get("/api/email-template")).json()
        invalid = await http.post("/api/email-template", json={"subject": "", "body": "x"})
        assert_error(invalid, 422, "validation")
        edited = await http.post(
            "/api/email-template", json={"subject": "Neuer Betreff", "body": "Neuer Text"}
        )
        assert edited.status_code == 200 and edited.json()["updated_at"] > template["updated_at"]

        preview = (await http.get(f"{url}/email-preview")).json()
        assert preview["subject"] == "Neuer Betreff" and preview["recipient_source"] == "missing"
        send = {
            "recipient": "client@example.test",
            "subject": "Final",
            "body": "Geänderter Text",
            "confirmed": True,
            "template_updated_at": preview["template_updated_at"],
        }
        unconfirmed = await http.post(f"{url}/send", json=send | {"confirmed": False})
        assert_error(unconfirmed, 422, "validation")
        bad_address = await http.post(f"{url}/send", json=send | {"recipient": "a b@x"})
        assert_error(bad_address, 422, "validation")

        mail.error = MailError("rejected")
        failed = await http.post(f"{url}/send", json=send)
        body = assert_error(failed, 502, "send_failed")
        failed_id = body["outreach_id"]
        mail.error = DeliveryUncertain("timeout")
        uncertain = await http.post(f"{url}/send", json=send)
        pending_id = assert_error(uncertain, 409, "send_pending")["outreach_id"]
        blocked = await http.post(f"{url}/send", json=send)
        assert assert_error(blocked, 409, "send_pending")["outreach_id"] == pending_id
        assert_error(await http.get(f"{url}/email-preview"), 409, "send_pending")
        assert_error(await http.post(f"{url}/dismiss"), 409, "wrong_state")
        assert len(mail.calls) == 2  # the blocked attempt never reached the provider

        await repository.finish_outreach(pending_id, "failed")  # the deliberate resolution
        mail.error = None
        sent = await http.post(f"{url}/send", json=send)
        assert sent.status_code == 200, sent.text
        assert sent.json()["sent_body"] == "Geänderter Text" and sent.json()["status"] == "sent"
        again = await http.post(f"{url}/send", json=send)
        assert assert_error(again, 409, "already_sent")["outreach_id"] == sent.json()["id"]
        assert (await http.get(url)).json()["status"] == "actioned"

        history = (await http.get("/api/outreach", params={"alert_id": alert["id"]})).json()
        assert [o["status"] for o in history["outreach"]] == ["sent", "failed", "failed"]
        assert failed_id in [o["id"] for o in history["outreach"]]
        kinds = [e["kind"] for e in (await http.get("/api/audit")).json()["entries"] if e["kind"]]
        assert "email_sent" in kinds and "template_edited" in kinds

        spec = (await http.get("/openapi.json")).json()
        contract = json.dumps([spec["paths"], spec["components"]]).casefold()
        assert "follow_up" not in contract  # the calendar records replies by hand only
        assert "reminder" not in contract


async def test_dismiss_with_reason_and_not_found(settings, schema, project):
    context, _ = await screened_api(settings, schema, project)
    async with context as http:
        await job_done(http, await http.post("/api/screenings", json={"company_id": project}))
        (alert,) = (await http.get("/api/alerts")).json()["alerts"]
        dismissed = await http.post(
            f"/api/alerts/{alert['id']}/dismiss", json={"reason": "bereits besprochen"}
        )
        assert dismissed.json()["status"] == "dismissed"
        assert dismissed.json()["dismissed_reason"] == "bereits besprochen"
        counts = (await http.get("/api/alerts", params={"status": "dismissed"})).json()
        assert [a["id"] for a in counts["alerts"]] == [alert["id"]]
        assert_error(await http.get("/api/alerts/999"), 404, "not_found")
        assert_error(await http.get("/api/alerts/999/email-preview"), 404, "not_found")


# Findings 6 and 8 through HTTP ------------------------------------------------------------


async def test_recover_endpoint_and_progress(settings, schema, project):
    context, repository = await screened_api(settings, schema, project)
    async with context as http:
        progress = (await http.get(f"/api/companies/{project}/progress")).json()
        assert progress["recovery"] == {
            "available": True,
            "action": "cleanup",
            "reason": "embedded, working data not cleaned up",
        }
        started = await http.post(f"/api/companies/{project}/recover")
        assert started.status_code == 202 and started.json()["action"] == "cleanup"
        await http.services.join()
        job = (await http.get(started.headers["location"])).json()
        assert job["id"] == started.json()["job"]["id"] and job["status"] == "succeeded"
        nothing = await http.post(f"/api/companies/{project}/recover")
        assert nothing.status_code == 200 and nothing.json()["job"] is None
        assert_error(await http.post("/api/companies/nope/recover"), 404, "not_found")
        repository.states[project].state = ProjectState.PUBLISHED
        assert_error(await http.post(f"/api/companies/{project}/recover"), 409, "wrong_state")


async def test_failed_rerun_does_not_expose_the_older_draft(settings, schema, tmp_path):
    repository = MemoryRepository(settings)
    async with api(settings, schema, repository=repository) as http:
        company_id = (await upload(http, room(tmp_path))).json()["company_id"]
        await finished(http, company_id)
        assert (await http.get(f"/api/companies/{company_id}/draft")).status_code == 200

        async def failing(draft):
            raise RuntimeError("draft table locked")

        repository.save_profile_draft = failing
        assert (await http.post(f"/api/companies/{company_id}/rerun")).status_code == 202
        progress = await finished(http, company_id)
        assert progress["run"]["state"] == "failed" and not progress["run"]["published"]
        assert "draft table locked" in progress["run"]["last_error"]
        assert_error(await http.get(f"/api/companies/{company_id}/draft"), 409, "wrong_state")


async def test_first_publication_failure_leaves_nothing_to_review(settings, schema, tmp_path):
    repository = MemoryRepository(settings)

    async def failing(*args):
        raise RuntimeError("publish failed")

    repository.publish_profile = failing
    async with api(settings, schema, repository=repository) as http:
        company_id = (await upload(http, room(tmp_path))).json()["company_id"]
        progress = await finished(http, company_id)
        assert progress["run"]["state"] == "failed" and not progress["run"]["published"]
        assert progress["project"]["state"] is None
        assert_error(await http.get(f"/api/companies/{company_id}/draft"), 409, "wrong_state")


def test_openapi_file_matches_the_app(settings, schema):
    """The checked-in contract is regenerated after every API change."""
    from app.api.openapi import build_spec

    stored = json.loads((Path(__file__).parents[1] / "openapi.json").read_text("utf-8"))
    assert stored == build_spec()


# Postgres acceptance: the whole workflow through HTTP --------------------------------------


@pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")
async def test_full_workflow_postgres(settings, tmp_path):
    """Upload DD documents, confirm, poll cleanup, ingest a legal PDF, screen, inspect
    alerts, edit and preview the template, send through log mode, read outreach and audit,
    then update a relevant fact with automatic re-screening and a contact-only fact."""
    profil_schema = load_profile_schema(PROFILE_SCHEMA)
    settings = settings.model_copy(
        update={
            "repository": "postgres",
            "database_url": SecretStr(DATABASE_URL),
            "profile_schema_path": PROFILE_SCHEMA,
            "screen_after_update": True,
            "mail_mode": "log",
        }
    )
    sentence = {"text": EMBED_SENTENCE}
    sold = "Die Gesellschaft hat ihre ausländische Tochtergesellschaft veräußert."

    def llm(kwargs):
        response_format = kwargs.get("response_format")
        name = response_format["json_schema"]["name"] if response_format else None
        if name == "law_metadata":
            return json.dumps(METADATA)
        if name == "law_relevance":
            relevant = sold not in kwargs["messages"][1]["content"]
            return verdict(relevant, "Der Abschnitt betrifft den Sachverhalt.")
        if name is None:
            return sentence["text"]
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
    project = f"test-flow-{uuid.uuid4().hex[:8]}"
    client = FakeClient(responder=llm)
    law_id = None
    repository = OwnLaws(DATABASE_URL, settings=settings, filename=f"{project}.pdf")
    async with api(settings, profil_schema, client=client, repository=repository) as http:
        original = (await http.get("/api/email-template")).json()  # a shared singleton
        try:
            company_id = (await upload(http, files, project_name=project)).json()["company_id"]
            assert (await finished(http, company_id))["project"]["state"] == "published"
            draft = (await http.get(f"/api/companies/{company_id}/draft")).json()
            finding = next(f for f in draft["draft"]["fields"] if f["name"] == "finding")
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
            confirm = await http.post(
                f"/api/companies/{company_id}/confirm",
                json={
                    "draft_version": draft["draft_version"],
                    "decisions": fill_template(template)["decisions"],
                },
            )
            assert confirm.status_code == 200, confirm.text
            progress = await finished(http, company_id)
            assert progress["project"]["state"] == "cleaned", progress
            assert progress["latest_job"]["operation"] == "advance"
            assert progress["latest_job"]["status"] == "succeeded"
            assert progress["screening"]["unscreened_laws"] is not None
            assert not (settings.data_dir / "uploads" / company_id).exists()

            # Finding 9: the confirmed profile keeps the finding norms of the review.
            profile = (await http.get(f"/api/companies/{company_id}/profile")).json()
            reviewed = {
                tuple(sorted(i["value"].get("normen") or []))
                for i in finding["items"]
                if i.get("value")
            }
            confirmed = {tuple(r["values"]["normen"]) for r in profile["lists"]["finding"]}
            assert confirmed == reviewed or confirmed <= reviewed | {()}
            assert all(
                r["values"]["normen"] == sorted(r["values"]["normen"])
                for r in profile["lists"]["finding"]
            )

            law = await job_done(
                http, await http.post("/api/laws", files=law_upload(tmp_path, f"{project}.pdf"))
            )
            assert law["status"] == "succeeded", law
            law_id = law["result"]["law_id"]
            screen = await job_done(
                http,
                await http.post(
                    "/api/screenings", json={"company_id": company_id, "law_id": law_id}
                ),
            )
            assert screen["status"] == "succeeded" and screen["result"]["complete"], screen
            hits = (
                await http.get(
                    f"/api/companies/{company_id}/matches",
                    params={"law_id": law_id, "only_hits": "true"},
                )
            ).json()["matches"]
            assert hits
            listing = (await http.get("/api/alerts", params={"company_id": company_id})).json()
            alert = listing["alerts"][0]
            assert listing["counts"]["new"] == len(hits)

            edited = await http.post(
                "/api/email-template",
                json={"subject": f"Betreff {project}", "body": "Sehr geehrte Damen und Herren"},
            )
            assert edited.status_code == 200
            preview = (await http.get(f"/api/alerts/{alert['id']}/email-preview")).json()
            assert preview["subject"] == f"Betreff {project}" and preview["mail_mode"] == "log"
            send = {
                "recipient": "client@example.test",
                "subject": preview["subject"],
                "body": preview["body"] + "\nMit Gruß",
                "confirmed": True,
                "template_updated_at": preview["template_updated_at"],
            }
            sent = await http.post(f"/api/alerts/{alert['id']}/send", json=send)
            assert sent.status_code == 200, sent.text
            assert sent.json()["sent_body"] == send["body"]
            assert list((settings.data_dir / "mail").glob("log-*.json"))
            again = await http.post(f"/api/alerts/{alert['id']}/send", json=send)
            assert_error(again, 409, "already_sent")
            history = (await http.get("/api/outreach", params={"company_id": company_id})).json()
            assert [o["status"] for o in history["outreach"]] == ["sent"]
            audit = (await http.get("/api/audit", params={"company_id": company_id})).json()
            assert "email_sent" in [e["kind"] for e in audit["entries"]]

            # Finding 5: a relevant update is refreshed and re-screened by the job.
            url = f"/api/companies/{company_id}/profile/update"
            sentence["text"] = sold
            change = {
                "field": "merkmal",
                "key": {"merkmal_code": "B07"},
                "kind": "edit",
                "cells": {"kurztext": "Tochter verkauft"},
            }
            updated = await http.post(url, json={"profile_version": 1, "changes": [change]})
            assert updated.status_code == 200 and updated.json()["embeddings_stale"]
            progress = await finished(http, company_id)
            assert progress["latest_job"]["operation"] == "refresh", progress
            assert progress["latest_job"]["status"] == "succeeded", progress["latest_job"]["error"]
            assert not progress["project"]["embeddings_stale"]
            assert progress["screening"]["pending_changes"] == 0
            matches = (
                await http.get(f"/api/companies/{company_id}/matches", params={"law_id": law_id})
            ).json()["matches"]
            assert matches and all(m["is_current"] for m in matches)
            assert not any(m["judge_relevant"] for m in matches)
            states = {
                a["id"]: a["status"]
                for a in (
                    await http.get(
                        "/api/alerts",
                        params={"company_id": company_id, "status": list(_STATUSES)},
                    )
                ).json()["alerts"]
            }
            assert states.pop(alert["id"]) == "actioned"
            assert set(states.values()) <= {"obsolete"}

            # A contact-only edit starts no job and keeps decisions current.
            jobs_before = progress["latest_job"]["id"]
            contact = await http.post(
                url,
                json={
                    "profile_version": 2,
                    "changes": [
                        {
                            "field": "ansprechpartner_email",
                            "kind": "edit",
                            "value": "kontakt@example.test",
                        }
                    ],
                },
            )
            assert contact.status_code == 200 and not contact.json()["embeddings_stale"]
            progress = await finished(http, company_id)
            assert progress["latest_job"]["id"] == jobs_before
            current = (
                await http.get(f"/api/companies/{company_id}/matches", params={"law_id": law_id})
            ).json()["matches"]
            assert len(current) == len(matches)
            assert progress["screening"]["pending_changes"] == 0  # nothing to re-screen
        finally:
            await http.post(
                "/api/email-template",
                json={"subject": original["subject"], "body": original["body"]},
            )
            if law_id is not None:
                async with await psycopg.AsyncConnection.connect(
                    DATABASE_URL, autocommit=True
                ) as conn:
                    await conn.execute(
                        "DELETE FROM screening.outreach WHERE company_id = %s", (project,)
                    )
                    await conn.execute(
                        "DELETE FROM screening.alert WHERE company_id = %s", (project,)
                    )
                    await conn.execute(
                        "DELETE FROM screening.match WHERE company_id = %s", (project,)
                    )
                    await conn.execute(
                        "DELETE FROM screening.coverage WHERE company_id = %s", (project,)
                    )
                    await conn.execute("DELETE FROM screening.law WHERE id = %s", (law_id,))


_STATUSES = ("new", "seen", "dismissed", "actioned", "obsolete")


class OwnLaws(PostgresRepository):
    """Re-screening covers every stored law; the shared test database also holds sources of
    other tests (other dimensions), so this test sees only the source it uploads."""

    def __init__(self, url, *, settings, filename):
        super().__init__(url, settings=settings)
        self.filename = filename

    async def list_laws(self):
        return [law for law in await super().list_laws() if law.filename == self.filename]

    async def list_law_sections(self, law_id=None):
        own = {law.id for law in await self.list_laws()}
        return [s for s in await super().list_law_sections(law_id) if s.law_id in own]


@pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")
async def test_confirmed_profile_lists_finding_norms_postgres(
    pg_repository, profil_schema, settings, project
):
    from app.services.confirm import confirm_profile
    from tests.profil_data import decide_all
    from tests.test_confirm import published

    view = await published(pg_repository, project, profil_schema, settings)
    await confirm_profile(pg_repository, profil_schema, settings, decide_all(view))
    profile = await pg_repository.read_profile(project, profil_schema, settings)
    (finding,) = profile.lists["finding"]
    assert finding.values["normen"] == ["§ 10a GewStG", "§ 8c KStG"]
    assert finding.fundstelle  # citations stay

    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        await conn.execute("DELETE FROM profil.finding_norm WHERE finding_id = %s", (finding.id,))
    profile = await pg_repository.read_profile(project, profil_schema, settings)
    assert profile.lists["finding"][0].values["normen"] == []


async def test_missing_mail_configuration_answers_503_and_reserves_nothing(
    settings, schema, project
):
    smtp = settings.model_copy(update={"mail_mode": "smtp", "gmail_address": ""})
    repository = await ready(smtp, project)
    async with api(smtp, schema, repository=repository) as http:
        await job_done(http, await http.post("/api/screenings", json={"company_id": project}))
        (alert,) = (await http.get("/api/alerts")).json()["alerts"]
        send = {"recipient": "a@example.test", "subject": "S", "body": "B", "confirmed": True}
        response = await http.post(f"/api/alerts/{alert['id']}/send", json=send)
        assert_error(response, 503, "mail_unavailable")
        assert (await http.get("/api/outreach")).json()["outreach"] == []
