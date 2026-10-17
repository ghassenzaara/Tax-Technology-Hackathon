"""Load the profile schema file and derive the agent's response model from it.

The schema is an input, never hardcoded. The default file `profil_schema.json` mirrors the
teammate's DB schema (`backend/db_scheme/`, Postgres schema `profil`): every field names its
target table, and list fields are rows of one table (for example one `veranlagung` row per
year and tax type). Format:

    {"document_classes": {"bescheid": 1, ...},          # code -> rank (1 = strongest)
     "fields": [
       {"name", "type", "description", "enum"?, "unit"?, "multiple"?, "table"?, "required"?},
       {"name", "type": "list", "description", "table", "key": [...], "items": [...],
        "parent_column"?}]}

`required` mirrors NOT NULL in the DB and is used by the writer only; the model may always
answer null. Item fields with `"db": false` help the writer link rows and are not columns.
"""

import json
import re
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, create_model

from app.models.extraction import Confidence

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
PROVENANCE_KEYS = frozenset({"source_page", "evidence", "confidence"})
DOCUMENT_KEY = "dokument"  # per-document metadata in the agent output, not a profile field


class SchemaError(ValueError):
    """The profile schema file is unusable. The message names the offending field."""


class FieldType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"  # ISO 8601, YYYY-MM-DD
    ENUM = "enum"
    LIST = "list"  # rows of one table; items are scalar fields


NUMERIC_TYPES = frozenset({FieldType.INTEGER, FieldType.NUMBER})


class FieldSpec(BaseModel):
    name: str
    type: FieldType
    description: str
    enum: list[str] | None = None
    unit: str | None = None
    multiple: bool = False  # array of values (Postgres array column)
    table: str | None = None
    required: bool = False
    db: bool = True
    key: list[str] | None = None
    items: list["FieldSpec"] | None = None
    parent_column: str | None = None

    @property
    def is_list(self) -> bool:
        return self.type is FieldType.LIST

    @property
    def rounds(self) -> bool:
        """Monetary amounts compare with the rounding rule; all other numbers exactly."""
        return self.type in NUMERIC_TYPES and (self.unit or "").upper() == "EUR"

    def item(self, name: str) -> "FieldSpec":
        return next(i for i in self.items or [] if i.name == name)


class ProfileSchema(BaseModel):
    fields: list[FieldSpec]
    document_classes: dict[str, int] = {}

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.fields]

    def field(self, name: str) -> FieldSpec:
        return next(f for f in self.fields if f.name == name)


def load_profile_schema(path: Path) -> ProfileSchema:
    try:
        raw = json.loads(Path(path).read_text("utf-8"))
    except OSError as exc:
        raise SchemaError(f"cannot read profile schema {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SchemaError(f"profile schema {path} is not valid JSON: {exc}") from exc
    return parse_profile_schema(raw)


def parse_profile_schema(raw: Any) -> ProfileSchema:
    """Validate by hand so every error names the field it is about."""
    if not isinstance(raw, dict) or not isinstance(raw.get("fields"), list) or not raw["fields"]:
        raise SchemaError("profile schema needs a non-empty 'fields' list")
    classes = raw.get("document_classes", {})
    if not isinstance(classes, dict) or not all(
        isinstance(k, str) and isinstance(v, int) for k, v in classes.items()
    ):
        raise SchemaError("'document_classes' must map class codes to integer ranks")
    fields = _parse_fields(raw["fields"], context="field", nested=False)
    if DOCUMENT_KEY in {f.name for f in fields}:
        raise SchemaError(f"field '{DOCUMENT_KEY}': name is reserved")
    return ProfileSchema(fields=fields, document_classes=classes)


def _parse_fields(raw_fields: list, *, context: str, nested: bool) -> list[FieldSpec]:
    fields: list[FieldSpec] = []
    seen: set[str] = set()
    for position, item in enumerate(raw_fields, start=1):
        if not isinstance(item, dict):
            raise SchemaError(f"{context} #{position}: must be an object")
        name = item.get("name")
        if not isinstance(name, str) or not _NAME.match(name):
            raise SchemaError(f"{context} #{position}: name {name!r} must match {_NAME.pattern}")
        label = f"{context} '{name}'"
        if name in seen:
            raise SchemaError(f"{label}: duplicate field name")
        if nested and name in PROVENANCE_KEYS:
            raise SchemaError(f"{label}: name is reserved")
        seen.add(name)

        type_ = item.get("type")
        if not isinstance(type_, str) or type_ not in set(FieldType):
            supported = ", ".join(FieldType)
            raise SchemaError(f"{label}: unsupported type {type_!r} (supported: {supported})")
        if nested and type_ == FieldType.LIST:
            raise SchemaError(f"{label}: lists cannot be nested")  # TODO: if the schema needs it
        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            raise SchemaError(f"{label}: description is required")

        enum = item.get("enum")
        if type_ == FieldType.ENUM:
            if not isinstance(enum, list) or not enum:
                raise SchemaError(f"{label}: enum needs a non-empty 'enum' list")
            if not all(isinstance(v, str) and v for v in enum) or len(set(enum)) != len(enum):
                raise SchemaError(f"{label}: enum values must be unique non-empty strings")
        elif enum is not None:
            raise SchemaError(f"{label}: 'enum' is only allowed for type enum")
        for flag in ("multiple", "required", "db"):
            if flag in item and not isinstance(item[flag], bool):
                raise SchemaError(f"{label}: '{flag}' must be true or false")
        for text in ("unit", "table", "parent_column"):
            if item.get(text) is not None and not isinstance(item[text], str):
                raise SchemaError(f"{label}: '{text}' must be a string")

        items = key = None
        if type_ == FieldType.LIST:
            if not isinstance(item.get("items"), list) or not item["items"]:
                raise SchemaError(f"{label}: list needs a non-empty 'items' list")
            items = _parse_fields(item["items"], context=f"{label} item", nested=True)
            key = item.get("key")
            item_names = {i.name for i in items}
            if not isinstance(key, list) or not key or not set(key) <= item_names:
                raise SchemaError(f"{label}: 'key' must list item field names")
        elif item.get("multiple") and type_ == FieldType.BOOLEAN:
            raise SchemaError(f"{label}: boolean fields cannot be multiple")

        fields.append(
            FieldSpec(
                name=name,
                type=type_,
                description=description.strip(),
                enum=enum,
                unit=item.get("unit"),
                multiple=item.get("multiple", False),
                table=item.get("table"),
                required=item.get("required", False),
                db=item.get("db", True),
                key=key,
                items=items,
                parent_column=item.get("parent_column"),
            )
        )
    return fields


# Response model and strict JSON Schema. Both describe the same shape:
#   {"dokument": {klasse_code, titel, dokument_datum},
#    <scalar field>: null | {value, source_page, evidence, confidence},
#    <list field>: [{<item fields, each nullable>, source_page, evidence, confidence}]}

_STRICT = ConfigDict(extra="forbid")
_PROVENANCE = {
    "source_page": (int, ...),
    "evidence": (str, ...),
    "confidence": (Confidence, ...),
}


def _python_type(spec: FieldSpec) -> Any:
    match spec.type:
        case FieldType.STRING:
            base = str
        case FieldType.INTEGER:
            base = int
        case FieldType.NUMBER:
            base = float
        case FieldType.BOOLEAN:
            base = bool
        case FieldType.DATE:
            base = date
        case FieldType.ENUM:
            base = Literal[tuple(spec.enum)]
    return list[base] if spec.multiple else base


def _document_model(schema: ProfileSchema) -> type[BaseModel]:
    klasse = Literal[tuple(schema.document_classes)] if schema.document_classes else str
    return create_model(
        "DocumentMeta",
        __config__=_STRICT,
        klasse_code=(klasse, ...),
        titel=(str | None, ...),
        dokument_datum=(date | None, ...),
    )


def build_response_model(schema: ProfileSchema) -> type[BaseModel]:
    """Pydantic model that validates the agent output. Every key required."""
    fields: dict[str, Any] = {DOCUMENT_KEY: (_document_model(schema), ...)}
    for spec in schema.fields:
        if spec.is_list:
            item = create_model(
                f"Item_{spec.name}",
                __config__=_STRICT,
                **{i.name: (_python_type(i) | None, ...) for i in spec.items},
                **_PROVENANCE,
            )
            fields[spec.name] = (list[item], ...)
        else:
            entry = create_model(
                f"Entry_{spec.name}",
                __config__=_STRICT,
                value=(_python_type(spec), ...),
                **_PROVENANCE,
            )
            fields[spec.name] = (entry | None, ...)
    return create_model("ProfileExtraction", __config__=_STRICT, **fields)


def _value_json_schema(spec: FieldSpec) -> dict[str, Any]:
    match spec.type:
        case FieldType.DATE:
            base = {"type": "string", "description": "ISO date YYYY-MM-DD"}
        case FieldType.ENUM:
            base = {"type": "string", "enum": list(spec.enum)}
        case _:
            base = {"type": str(spec.type)}
    return {"type": "array", "items": base} if spec.multiple else base


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_PROVENANCE_JSON = {
    "source_page": {"type": "integer"},
    "evidence": {"type": "string"},
    "confidence": {"type": "string", "enum": [c.value for c in Confidence]},
}


def response_json_schema(schema: ProfileSchema) -> dict[str, Any]:
    """Strict-mode JSON Schema for OpenAI structured outputs: all properties required,
    no additional properties, nullability as anyOf(x, null)."""
    klasse: dict[str, Any] = {"type": "string"}
    if schema.document_classes:
        klasse["enum"] = list(schema.document_classes)
    properties: dict[str, Any] = {
        DOCUMENT_KEY: _object(
            {
                "klasse_code": klasse,
                "titel": _nullable({"type": "string"}),
                "dokument_datum": _nullable({"type": "string", "description": "YYYY-MM-DD"}),
            }
        )
    }
    for spec in schema.fields:
        if spec.is_list:
            item = {i.name: _nullable(_value_json_schema(i)) for i in spec.items}
            properties[spec.name] = {
                "type": "array",
                "items": _object(item | _PROVENANCE_JSON),
            }
        else:
            entry = _object({"value": _value_json_schema(spec)} | _PROVENANCE_JSON)
            properties[spec.name] = _nullable(entry)
    return _object(properties)
