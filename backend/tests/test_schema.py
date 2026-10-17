import json

import pytest
from pydantic import ValidationError

from app.agents.prompt import build_system_prompt
from app.profile.schema import (
    DOCUMENT_KEY,
    SchemaError,
    build_response_model,
    load_profile_schema,
    parse_profile_schema,
    response_json_schema,
)
from tests.helpers import answer, entry, item


def field(name, type_="string", **extra):
    return {"name": name, "type": type_, "description": f"{name} description", **extra}


def listing(name="rows", key=("a",), items=None):
    items = items or [field("a"), field("b", "integer")]
    return field(name, "list", key=list(key), items=items)


def test_small_schema_loads(schema):
    assert schema.names == [
        "company_name",
        "legal_form",
        "employees",
        "annual_revenue_eur",
        "steuerattribut",
    ]
    assert schema.field("steuerattribut").key == ["art", "stichtag"]


def test_load_from_file(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps({"fields": [field("a"), field("b", "integer")]}), "utf-8")
    assert load_profile_schema(path).names == ["a", "b"]


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ([field("a"), field("a")], "field 'a': duplicate"),
        ([field("a", "object")], "field 'a': unsupported type 'object'"),
        ([field("a", "enum", enum=[])], "field 'a': enum needs"),
        ([field("a", "enum")], "field 'a': enum needs"),
        ([{"name": "a", "type": "string"}], "field 'a': description"),
        ([field("Bad Name")], "field #1"),
        ([field("a", "list")], "field 'a': list needs a non-empty 'items'"),
        ([listing(key=("zzz",))], "field 'rows': 'key' must list item field names"),
        ([listing(items=[field("a"), field("evidence")])], "item 'evidence': name is reserved"),
        ([listing(items=[field("a"), listing("inner")])], "item 'inner': lists cannot be nested"),
        ([field(DOCUMENT_KEY)], "is reserved"),
    ],
)
def test_invalid_schema_names_the_field(fields, message):
    with pytest.raises(SchemaError, match=message):
        parse_profile_schema({"fields": fields})


def test_response_model_matches_field_list(schema):
    model = build_response_model(schema)
    assert list(model.model_fields) == [DOCUMENT_KEY, *schema.names]

    parsed = model.model_validate_json(
        answer(
            employees=entry(25, evidence="25 Mitarbeitern"),
            legal_form=entry("GmbH"),
            steuerattribut=[item(art="kst_verlustvortrag", stichtag="2024-12-31", betrag_eur=1.5)],
        )
    )
    assert parsed.employees.value == 25 and parsed.company_name is None
    assert parsed.steuerattribut[0].betrag_eur == 1.5
    assert parsed.dokument.klasse_code == "bescheid"

    with pytest.raises(ValidationError):  # enum value not allowed
        model.model_validate_json(answer(legal_form=entry("Limited")))
    with pytest.raises(ValidationError):  # integer field with a fraction
        model.model_validate_json(answer(employees=entry(25.5)))
    with pytest.raises(ValidationError):  # unknown document class
        model.model_validate_json(
            answer(dokument={"klasse_code": "x", "titel": None, "dokument_datum": None})
        )
    with pytest.raises(ValidationError):  # field missing entirely
        model.model_validate({"company_name": None})


def test_strict_json_schema(schema):
    built = response_json_schema(schema)

    assert built["additionalProperties"] is False
    assert built["required"] == [DOCUMENT_KEY, *schema.names] == list(built["properties"])
    for name in ("company_name", "legal_form", "employees", "annual_revenue_eur"):
        obj, null = built["properties"][name]["anyOf"]
        assert null == {"type": "null"}
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
    rows = built["properties"]["steuerattribut"]
    assert rows["type"] == "array"
    row = rows["items"]
    assert row["additionalProperties"] is False and set(row["required"]) == set(row["properties"])
    assert {"source_page", "evidence", "confidence", "art"} <= set(row["properties"])
    assert row["properties"]["art"]["anyOf"][1] == {"type": "null"}  # item columns nullable
    assert built["properties"][DOCUMENT_KEY]["properties"]["klasse_code"]["enum"] == [
        "bescheid",
        "dd_report",
        "entwurf",
    ]


def test_multiple_values_become_arrays():
    parsed = parse_profile_schema(
        {
            "fields": [
                listing(items=[field("a"), field("arten", "enum", enum=["x", "y"], multiple=True)])
            ]
        }
    )
    row = response_json_schema(parsed)["properties"]["rows"]["items"]["properties"]["arten"]
    assert row["anyOf"][0] == {"type": "array", "items": {"type": "string", "enum": ["x", "y"]}}
    model = build_response_model(parsed)
    ok = {"dokument": {"klasse_code": "a", "titel": None, "dokument_datum": None}}
    model.model_validate(ok | {"rows": [item(a="1", arten=["x", "y"])]})


def test_prompt_contains_fields_and_is_document_independent(schema):
    two = parse_profile_schema(
        {"fields": [field("revenue", "number", unit="EUR"), field("kind", "enum", enum=["x", "y"])]}
    )
    prompt = build_system_prompt(two, evidence_max_chars=300)

    assert "revenue description" in prompt and "kind description" in prompt
    assert "unit: EUR" in prompt and "allowed: x | y" in prompt
    assert "300 characters" in prompt
    assert prompt == build_system_prompt(two, evidence_max_chars=300)  # no per-document parts

    with_list = build_system_prompt(schema, evidence_max_chars=300)
    assert "    - art (key) (enum" in with_list
    assert "Document classes (klasse_code): bescheid, dd_report, entwurf" in with_list
