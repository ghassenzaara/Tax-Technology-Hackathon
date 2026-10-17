"""The default profile schema must stay in sync with the teammate's SQL (backend/db_scheme):
every target table and column exists, NOT NULL columns are marked required, enums match the
CHECK constraints and types, and document classes match the vocabulary with their ranks."""

import re

import pytest

from app.config import PROFILE_DB_DIR
from app.profile.schema import FieldType

SQL = (PROFILE_DB_DIR / "01_schema.sql").read_text("utf-8")
VOCAB = (PROFILE_DB_DIR / "02_vokabular.sql").read_text("utf-8")


def tables() -> dict[str, str]:
    return {
        m.group(1): m.group(2) for m in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\);", SQL, re.S)
    }


def columns(body: str) -> dict[str, str]:
    """column -> its definition, including inline provenance columns."""
    result = {}
    body = re.sub(r"--[^\n]*", "", body)
    for part in re.split(r",\s*(?![^()]*\))", body):
        line = part.strip()
        match = re.match(r"(\w+)\s+(.+)", line, re.S)
        if match and match.group(1).upper() not in {"UNIQUE", "PRIMARY"}:
            result[match.group(1)] = match.group(2)
    return result


def types() -> dict[str, list[str]]:
    return {
        m.group(1): re.findall(r"'([^']+)'", m.group(2))
        for m in re.finditer(r"CREATE TYPE (\w+) AS ENUM \(([^)]*)\)", SQL)
    }


def allowed(definition: str) -> list[str] | None:
    check = re.search(r"CHECK \(\w+ IN \(([^)]*)\)\)", definition)
    if check:
        return re.findall(r"'([^']+)'", check.group(1))
    base = definition.split()[0].rstrip("[]")
    return types().get(base)


def target_columns(profil_schema):
    all_tables = tables()
    for spec in profil_schema.fields:
        table = spec.table or spec.name
        assert table in all_tables, f"{spec.name}: no table {table}"
        cols = columns(all_tables[table])
        specs = spec.items if spec.is_list else [spec]
        for s in specs:
            if s.db:
                yield f"{table}.{s.name}", s, cols.get(s.name)


def test_every_target_column_exists(profil_schema):
    missing = [name for name, _, definition in target_columns(profil_schema) if definition is None]
    assert missing == []


def test_required_matches_not_null(profil_schema):
    wrong = [
        name
        for name, spec, definition in target_columns(profil_schema)
        if ("NOT NULL" in definition and "DEFAULT" not in definition) != spec.required
    ]
    assert wrong == []


def test_enums_match_check_constraints(profil_schema):
    wrong = []
    for name, spec, definition in target_columns(profil_schema):
        db_values = allowed(definition)
        if spec.type is FieldType.ENUM and set(spec.enum) != set(db_values or spec.enum):
            wrong.append(f"{name}: {spec.enum} vs {db_values}")
        if spec.type is not FieldType.ENUM and db_values and name != "gesellschaft.rolle":
            wrong.append(f"{name}: DB restricts values {db_values}")
    assert wrong == []


def test_document_classes_match_vocabulary(profil_schema):
    block = VOCAB.split("INSERT INTO dokument_klasse")[1].split(";")[0]
    vocab = {code: int(rank) for code, rank in re.findall(r"\('(\w+)',\s*'[^']*',\s*(\d)\)", block)}
    assert profil_schema.document_classes == vocab


def test_finding_norms_are_vocabulary(profil_schema):
    block = VOCAB.split("INSERT INTO norm")[1].split(";")[0]
    norms = set(re.findall(r"\('([^']+)','", block))
    finding = profil_schema.field("finding").item("normen")
    assert set(finding.enum) <= norms
    exempt = profil_schema.field("ust_steuerfrei").item("norm_code")
    assert set(exempt.enum) <= norms


@pytest.mark.parametrize("code", ["B04", "B05", "B07", "C04", "C09", "E01", "E05b", "G02"])
def test_merkmal_codes_are_vocabulary(profil_schema, code):
    assert f"('{code}'," in VOCAB
    assert code in profil_schema.field("merkmal").item("merkmal_code").enum
