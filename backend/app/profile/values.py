"""Per-type value normalization and equality for the merge (step 7).

The rounding rule applies only to monetary amounts (fields with unit EUR, see
`FieldSpec.rounds`). Every other number (years, counts, percentages, key columns) must be
exactly equal: a trailing zero in 2020 or 60 is not a sign of rounding.

Rounding rule (TODO: confirm with the team). A number's granularity is the size of its last
significant digit: 0.01 for 1234.56, 1 for 1235, 1000 for 1235000. Granularity from trailing
zeros is capped at `max_granularity`, so 1000000 never silently matches 1234567. Two numbers
are equal when the more precise one, rounded half away from zero to the coarser one's
granularity, equals the coarser one. Numbers are compared as exact decimals, never floats.
"""

import json
import re
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.profile.schema import NUMERIC_TYPES, FieldSpec, FieldType

_WHITESPACE = re.compile(r"\s+")
_EDGE_PUNCTUATION = " \t\n.,;:!?\"'`´„“”‚‘’»«()[]{}-–—/"


def normalize_string(value: str) -> str:
    # TODO: alias table for German legal-form spellings (GmbH vs G.m.b.H.) once real data needs it.
    text = unicodedata.normalize("NFKC", value).casefold()
    return _WHITESPACE.sub(" ", text).strip(_EDGE_PUNCTUATION)


def to_decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str | Decimal):
        raise ValueError(f"not a number: {value!r}")
    try:
        number = Decimal(str(value))  # str() keeps the shortest exact repr of a float
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"not a finite number: {value!r}")
    return number


def granularity(number: Decimal, max_granularity: int) -> Decimal:
    if number.is_zero():
        return Decimal(1)
    step = Decimal(1).scaleb(number.normalize().as_tuple().exponent)
    return min(step, Decimal(max_granularity)) if step > 1 else step


def round_to(number: Decimal, step: Decimal) -> Decimal:
    return (number / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step


def numbers_equal(a: Any, b: Any, max_granularity: int) -> bool:
    da, db = to_decimal(a), to_decimal(b)
    ga, gb = granularity(da, max_granularity), granularity(db, max_granularity)
    (fine, _), (coarse, coarse_step) = sorted(((da, ga), (db, gb)), key=lambda item: item[1])
    return round_to(fine, coarse_step) == coarse


def values_equal(
    field_type: FieldType, a: Any, b: Any, max_granularity: int, *, rounding: bool = False
) -> bool:
    """`rounding` enables the rounding rule for numbers; only monetary fields pass it."""
    if isinstance(a, list) or isinstance(b, list):  # multiple values: same set
        if not (isinstance(a, list) and isinstance(b, list)):
            return False
        return {_canonical(field_type, x) for x in a} == {_canonical(field_type, x) for x in b}
    if field_type in NUMERIC_TYPES:
        if rounding:
            return numbers_equal(a, b, max_granularity)
        return to_decimal(a) == to_decimal(b)
    if field_type is FieldType.STRING:
        return normalize_string(a) == normalize_string(b)
    return a == b  # enum, boolean, ISO date: exact match


def is_valid(spec: FieldSpec, value: Any) -> bool:
    """Does a stored value fit its field? Lets the merge drop one corrupt row (for
    example "n/a" in a numeric field) instead of failing the whole draft."""
    if spec.type is FieldType.LIST:
        return isinstance(value, dict) and all(
            value.get(i.name) is None or is_valid(i, value[i.name]) for i in spec.items
        )
    if spec.multiple:
        return isinstance(value, list) and all(_valid_scalar(spec, v) for v in value)
    return _valid_scalar(spec, value)


def _valid_scalar(spec: FieldSpec, value: Any) -> bool:
    match spec.type:
        case FieldType.STRING:
            if spec.format == "email":
                from app.mail.validate import address

                try:
                    address(value)
                except ValueError:
                    return False
            return isinstance(value, str)
        case FieldType.ENUM:
            return isinstance(value, str) and value in (spec.enum or [])
        case FieldType.BOOLEAN:
            return isinstance(value, bool)
        case FieldType.DATE:
            try:
                date.fromisoformat(value)
            except (TypeError, ValueError):
                return False
            return True
        case FieldType.INTEGER | FieldType.NUMBER:
            try:
                to_decimal(value)
            except ValueError:
                return False
            return True
    return False


def _canonical(field_type: FieldType, value: Any) -> Any:
    if field_type in NUMERIC_TYPES:
        return to_decimal(value).normalize()
    if field_type is FieldType.STRING and isinstance(value, str):
        return normalize_string(value)
    return json.dumps(value, sort_keys=True)


def differs_by_rounding(field_type: FieldType, a: Any, b: Any) -> bool:
    """For two values already found equal: true when they are numbers that are not identical."""
    return (
        field_type in NUMERIC_TYPES
        and not isinstance(a, list)
        and a is not None
        and b is not None
        and to_decimal(a) != to_decimal(b)
    )


def precision(field_type: FieldType, value: Any, max_granularity: int) -> Decimal:
    """Granularity of a number (smaller = more precise); 0 for everything else."""
    if field_type in NUMERIC_TYPES and value is not None and not isinstance(value, list):
        return granularity(to_decimal(value), max_granularity)
    return Decimal(0)


def sort_key(field_type: FieldType, value: Any, max_granularity: int) -> tuple:
    """Finest granularity first, then a stable textual tie-breaker."""
    return (
        precision(field_type, value, max_granularity),
        json.dumps(value, sort_keys=True, ensure_ascii=False),
    )
