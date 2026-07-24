"""Prueft das dynamische Validierungsmodell und die Quarantaene-Codes (I0.8.3)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from kintsugi.packs.loader import load_pack
from kintsugi.validate.dynamic_model import validate_row


def _pack():
    return load_pack("books.toscrape.com", "book", root=Path("packs"))


def _clean() -> dict[str, object]:
    return {
        "title": "A Light in the Attic",
        "price": Decimal("51.77"),
        "currency": "GBP",
        "availability": 22,
        "upc": "a897fe39b1053632",
    }


def test_saubere_zeile_wird_akzeptiert():
    result = validate_row(_pack(), _clean())
    assert result.accepted
    assert result.reasons == []
    assert result.payload["price"] == Decimal("51.77")


def test_type_error_price():
    result = validate_row(_pack(), {**_clean(), "price": "keine-zahl"})
    assert not result.accepted
    assert "type_error:price" in result.reasons


def test_range_violation_price_wird_persistiert():
    result = validate_row(_pack(), {**_clean(), "price": Decimal("99999")})
    assert result.accepted  # persistiert mit Violation
    assert "range_violation:price" in result.reasons
    assert result.payload is not None


def test_enum_violation_currency():
    result = validate_row(_pack(), {**_clean(), "currency": "XYZ"})
    assert result.accepted
    assert "enum_violation:currency" in result.reasons


def test_natural_key_missing():
    result = validate_row(_pack(), {**_clean(), "upc": None})
    assert not result.accepted
    assert result.reasons == ["natural_key_missing"]


def test_titel_none_wird_akzeptiert_und_gezaehlt():
    """required=true ist eine Fill-Rate-Schwelle, kein Ablehnungsgrund."""
    result = validate_row(_pack(), {**_clean(), "title": None})
    assert result.accepted
    assert result.payload["title"] is None
    assert result.reasons == []


def test_upc_pattern_verletzung_ist_harter_reject():
    result = validate_row(_pack(), {**_clean(), "upc": "GROSSBUCHSTABEN!!"})
    assert not result.accepted
    assert "type_error:upc" in result.reasons
