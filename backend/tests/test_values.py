from decimal import Decimal

import pytest

from app.profile.schema import FieldType
from app.profile.values import granularity, normalize_string, numbers_equal, values_equal


@pytest.mark.parametrize(
    ("a", "b"),
    [(1234.5, 1235), (1235000, 1234567), (1235000.0, 1234567), (0.5, 0.5), (100, 100.0)],
)
def test_rounding_equal(a, b):
    assert numbers_equal(a, b, 1000) and numbers_equal(b, a, 1000)


@pytest.mark.parametrize(
    ("a", "b"),
    [(25, 26), (20, 25), (1000000, 1234567), (1200000, 1300000), (1234567, 1234567000)],
)
def test_rounding_conflict(a, b):
    assert not numbers_equal(a, b, 1000) and not numbers_equal(b, a, 1000)


@pytest.mark.parametrize(("a", "b"), [(2020, 2024), (2020, 2015), (60, 55), (1000, 1499)])
def test_non_monetary_numbers_compare_exactly(a, b):
    # Review finding 1: trailing zeros of years, quotas or counts are no rounding.
    assert not values_equal(FieldType.INTEGER, a, b, 1000)
    assert not values_equal(FieldType.NUMBER, a, b, 1000)
    assert values_equal(FieldType.INTEGER, a, a, 1000)
    assert values_equal(FieldType.NUMBER, 60, 60.0, 1000)


def test_rounding_only_when_asked():
    assert values_equal(FieldType.NUMBER, 1_235_000, 1_234_567, 1000, rounding=True)
    assert not values_equal(FieldType.NUMBER, 1_235_000, 1_234_567, 1000)


def test_only_eur_fields_round(profil_schema):
    assert profil_schema.field("kaufpreis_eur").rounds
    veranlagung = profil_schema.field("veranlagung")
    assert not veranlagung.item("jahr").rounds
    assert not profil_schema.field("beteiligung").item("quote_prozent").rounds
    assert profil_schema.field("steuerattribut").item("betrag_eur").rounds


def test_granularity_is_capped():
    assert granularity(Decimal("1234.56"), 1000) == Decimal("0.01")
    assert granularity(Decimal("1235"), 1000) == 1
    assert granularity(Decimal("1235000"), 1000) == 1000
    assert granularity(Decimal("1000000"), 1000) == 1000  # not 1,000,000


def test_string_normalization():
    assert normalize_string("  Muster  GmbH. ") == normalize_string("muster gmbh")
    assert values_equal(FieldType.STRING, "Muster GmbH", "MUSTER GMBH", 1000)
    assert not values_equal(FieldType.STRING, "Muster GmbH", "Muster AG", 1000)


def test_exact_types():
    assert values_equal(FieldType.ENUM, "GmbH", "GmbH", 1000)
    assert not values_equal(FieldType.ENUM, "GmbH", "gmbh", 1000)
    assert not values_equal(FieldType.DATE, "2023-12-31", "2023-12-30", 1000)
    assert values_equal(FieldType.BOOLEAN, True, True, 1000)
