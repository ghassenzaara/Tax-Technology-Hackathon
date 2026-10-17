import json
import os
import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr

from app import cli
from app.config import PROFILE_SCHEMA
from app.ingest import pipeline
from tests.helpers import (
    SENTENCE,
    FakeClient,
    answer,
    entry,
    item,
    make_pdf,
    make_simple_excel,
)

ANSWER = answer(employees=entry(25, evidence="Restaurant in Berlin mit 25 Mitarbeitern"))


def responder(kwargs):
    if "response_format" not in kwargs:  # OCR transcription call
        return SENTENCE
    return ANSWER


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "dd"
    make_pdf(root / "text.pdf", [SENTENCE])
    make_pdf(root / "scan.pdf", [None])
    make_simple_excel(root / "data.xlsx")
    (root / "corrupt.pdf").write_bytes(b"%PDF-1.4\nbroken")
    return root


@pytest.fixture
def keyed(settings, monkeypatch):
    keyed = settings.model_copy(update={"openai_api_key": "sk-test"})
    monkeypatch.setattr(cli, "get_settings", lambda: keyed)
    monkeypatch.setattr(pipeline, "make_client", lambda s: FakeClient(responder=responder))
    return keyed


def test_extract_end_to_end(folder, tmp_path, keyed, capsys):
    out = tmp_path / "out.json"

    code = cli.main([str(folder), "--extract", "--company-id", "c1", "--json", str(out)])

    assert code == 0
    printed = capsys.readouterr().out
    assert "Profile draft for c1" in printed and "documents extracted in" in printed
    payload = json.loads(out.read_text("utf-8"))
    status = {e["filename"]: e["status"] for e in payload["extractions"]}
    assert status == {
        "text.pdf": "ok",
        "scan.pdf": "ok",
        "data.xlsx": "ok",
        "corrupt.pdf": "skipped",
    }
    employees = next(f for f in payload["profile_draft"]["fields"] if f["name"] == "employees")
    assert employees["status"] == "confirmed" and employees["value"] == 25
    assert [d["filename"] for d in payload["profile_draft"]["incomplete_documents"]] == [
        "corrupt.pdf"
    ]


def test_parse_only_unchanged(folder, settings, monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    assert cli.main([str(folder)]) == 0
    printed = capsys.readouterr().out
    assert "STATUS" in printed and "Profile draft" not in printed


def test_extract_without_key_fails_fast(folder, settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    assert cli.main([str(folder), "--extract"]) == 2


def test_bad_schema_fails_fast(folder, tmp_path, keyed):
    bad = tmp_path / "schema.json"
    bad.write_text('{"fields": [{"name": "a", "type": "list", "description": "x"}]}', "utf-8")

    assert cli.main([str(folder), "--extract", "--schema", str(bad)]) == 2


def test_postgres_without_url_fails_fast(folder, keyed):
    assert cli.main([str(folder), "--extract", "--repository", "postgres"]) == 2


def test_publish_needs_postgres(folder, keyed):
    assert cli.main([str(folder), "--extract", "--publish"]) == 2


def test_publish_without_extract_or_merge_fails(folder, keyed, monkeypatch):
    # Review finding 5: no silently ignored options.
    postgres = keyed.model_copy(
        update={"repository": "postgres", "database_url": SecretStr("postgresql://x/y")}
    )
    monkeypatch.setattr(cli, "get_settings", lambda: postgres)

    assert cli.main([str(folder), "--publish"]) == 2


def test_default_company_id_is_folder_name(folder, keyed, capsys):
    # Review finding 9: reruns on one folder reuse one id instead of piling up.
    assert cli.main([str(folder), "--extract"]) == 0
    assert "Profile draft for dd " in capsys.readouterr().out


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_init_db_with_folder_but_no_extract_sets_up_tables(folder, keyed, monkeypatch, capsys):
    # Review finding 5: --init-db next to a folder is honoured, not ignored.
    postgres = keyed.model_copy(
        update={
            "repository": "postgres",
            "database_url": SecretStr(os.environ["TEST_DATABASE_URL"]),
        }
    )
    monkeypatch.setattr(cli, "get_settings", lambda: postgres)

    assert cli.main([str(folder), "--init-db"]) == 0
    assert "pipeline tables ready" in capsys.readouterr().out


MOCK_FILES = Path(__file__).parents[1] / "mock_files"
DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
CLASSES = {  # filename fragment -> klasse_code, like the agent would classify
    "Tax-DD-Report": "dd_report",
    "SPA": "spa",
    "Handelsregister": "register",
    "Gesellschafterlisten": "register",
    "organigramm": "organigramm",
    "ENTWURF": "entwurf",
    "bescheide": "bescheid",
    "Steuererklaerungen": "erklaerung",
    "Jahresabschluss": "jahresabschluss",
    "Einspruch": "rechtsbehelf",
    "Betriebspruefung": "aussenpruefung",
    "Darlehen": "vertrag",
    "Leasing": "vertrag",
    "Grundbuch": "grundbuch",
    "Einbringung": "vertrag",
    "Datenraum": "qa_index",
}


def profil_answer(kwargs, profil_schema) -> str:
    """A fake agent answer for the real profile schema: a few facts, everything else
    null or []. The document class comes from the file name in the markdown header."""
    header = kwargs["messages"][1]["content"].split("\n")[0]
    klasse = next(code for fragment, code in CLASSES.items() if fragment in header)
    payload = {f.name: [] if f.is_list else None for f in profil_schema.fields}
    payload |= {
        "dokument": {"klasse_code": klasse, "titel": header[12:], "dokument_datum": None},
        "deal_typ": entry("share_deal", evidence="Geschäftsanteilskauf"),
        "kaeufer": entry("Weserlinie Logistics BidCo GmbH", evidence="Weserlinie"),
        "name": entry("Nordhafen Logistik GmbH (Test)", evidence="Nordhafen Logistik GmbH"),
        "rechtsform": entry("GmbH", evidence="GmbH"),
        "rolle": entry("operativ", evidence="operativ"),
        "merkmal": [
            item(merkmal_code="B04", wert="nein", kurztext="Kein GAV eingetragen", evidence="x")
        ],
    }
    return json.dumps(payload)


@pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")
def test_mock_data_room_end_to_end_with_publish(keyed, monkeypatch, profil_schema, capsys):
    """Real mock documents -> parse -> (fake) agents -> merge -> publish into schema profil."""
    project = f"test-e2e-{uuid.uuid4().hex[:8]}"
    settings = keyed.model_copy(
        update={
            "repository": "postgres",
            "database_url": SecretStr(DATABASE_URL),
            "profile_schema_path": PROFILE_SCHEMA,
        }
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    client = FakeClient(responder=lambda kwargs: profil_answer(kwargs, profil_schema))
    monkeypatch.setattr(pipeline, "make_client", lambda s: client)

    code = cli.main(
        [str(MOCK_FILES), "--extract", "--publish", "--init-db", "--company-id", project]
    )

    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "17 documents extracted in" in printed
    assert f"Published project '{project}' to schema profil" in printed
    assert "dokument=17" in printed and "gesellschaft=1" in printed
    assert "kaeufer" in printed and "confirmed" in printed


def test_steps_need_postgres_and_arguments(keyed, tmp_path):
    decisions = tmp_path / "decisions.json"
    decisions.write_text("{}", "utf-8")

    assert cli.main(["--review", "--company-id", "x"]) == 2  # memory repository
    assert cli.main(["--confirm", str(decisions), "--company-id", "x"]) == 2
    assert cli.main(["--embed", "--cleanup", "--company-id", "x"]) == 2
    assert cli.main(["--review"]) == 2


def fill_template(template: dict) -> dict:
    """What the advisor does in the UI: decide every open point."""
    for d in template["decisions"]:
        if "choose" in d["allowed"]:
            d["action"], d["group"] = "choose", 0
        elif "leave_empty" in d["allowed"]:
            d["action"] = "leave_empty"
        else:
            d["action"] = "edit"
            d["cells"] = {name: {"group": 0} for name in d["cells"]}
        del d["allowed"]
    return template


@pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")
def test_review_confirm_advance_end_to_end(keyed, monkeypatch, profil_schema, capsys, tmp_path):
    """Mock data room -> publish -> review -> confirm -> embed + cleanup through the CLI."""
    project = f"test-steps-{uuid.uuid4().hex[:8]}"
    settings = keyed.model_copy(
        update={
            "repository": "postgres",
            "database_url": SecretStr(DATABASE_URL),
            "profile_schema_path": PROFILE_SCHEMA,
        }
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    def agent(kwargs):  # every document also states one foreign subsidiary (B07 ja)
        payload = json.loads(profil_answer(kwargs, profil_schema))
        payload["merkmal"].append(
            item(
                merkmal_code="B07", wert="ja", kurztext="Tochter in den Niederlanden", evidence="x"
            )
        )
        return json.dumps(payload)

    sentence = "Die Gesellschaft hält eine ausländische Tochtergesellschaft."
    client = FakeClient(
        responder=lambda kwargs: agent(kwargs) if "response_format" in kwargs else sentence
    )
    monkeypatch.setattr(pipeline, "make_client", lambda s: client)
    monkeypatch.setattr(cli, "make_client", lambda s: client)
    upload = settings.data_dir / project
    upload.mkdir(parents=True)
    (upload / "01_Tax-DD-Report.pdf").write_bytes(b"%PDF raw")

    assert cli.main([str(MOCK_FILES), "--extract", "--publish", "--company-id", project]) == 0
    review = tmp_path / "review.json"
    assert cli.main(["--review", "--company-id", project, "--json", str(review)]) == 0
    printed = capsys.readouterr().out
    assert "Open decisions:" in printed and "draft version" in printed
    decisions = tmp_path / "decisions.json"
    template = json.loads(review.read_text("utf-8"))["decisions_template"]
    decisions.write_text(json.dumps(fill_template(template)), "utf-8")

    confirm = ["--confirm", str(decisions), "--confirmed-by", "Dr. Test", "--company-id", project]
    assert cli.main(confirm) == 0
    assert f"Confirmed project '{project}' by Dr. Test" in capsys.readouterr().out
    assert cli.main(confirm) == 1  # a second confirm is refused (state confirmed)

    advance = ["--advance", "--company-id", project, "--delete-path", str(upload)]
    assert cli.main(advance) == 0
    printed = capsys.readouterr().out
    assert "1 features embedded in" in printed and sentence in printed
    assert f"Cleaned project '{project}'" in printed and not upload.exists()
    assert cli.main([str(MOCK_FILES), "--extract", "--publish", "--company-id", project]) == 1
