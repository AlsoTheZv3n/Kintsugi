"""Prueft parse_currency, int_from_text und die currency-Ableitung (I0.5.3)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from kintsugi.transform.primitives import (
    SYMBOL_TO_ISO,
    Money,
    currency_from_symbol,
    int_from_text,
    parse_currency,
)


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("£51.77", Decimal("51.77"), "GBP"),  # F2: live verifiziert
        ("CHF 1'299.00", Decimal("1299.00"), "CHF"),  # Schweizer Apostroph
        ("1.299,00 €", Decimal("1299.00"), "EUR"),  # Punkt tausend, Komma dezimal
        ("49,90 CHF", Decimal("49.90"), "CHF"),  # Komma dezimal
        ("$0.99", Decimal("0.99"), "USD"),
        ("GBP 51.77", Decimal("51.77"), "GBP"),  # Code statt Symbol
    ],
)
def test_parse_currency_werte(text, amount, currency):
    money = parse_currency(text)
    assert money is not None
    assert money.amount == amount
    assert money.currency == currency


def test_betrag_ist_immer_decimal():
    assert isinstance(parse_currency("£51.77").amount, Decimal)


@pytest.mark.parametrize("bad", ["51.77", None, "kostenlos", ""])
def test_ohne_symbol_none_ohne_ausnahme(bad):
    assert parse_currency(bad) is None


def test_codomain_ist_der_pack_enum():
    assert set(SYMBOL_TO_ISO.values()) == {"GBP", "CHF", "EUR", "USD"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("In stock (22 available)", 22),
        ("Out of stock", None),
        ("", None),
        (None, None),
        ("Seite 7 von 50", 7),
        ("-7 Grad", -7),
    ],
)
def test_int_from_text(text, expected):
    result = int_from_text(text)
    if expected is None:
        assert result is None
    else:
        assert result == expected


def test_int_out_of_stock_ist_none_nicht_null():
    """Explizit is None, nicht == 0 — sonst faelscht es einen Bestand von null."""
    result = int_from_text("Out of stock")
    assert result is None
    assert result != 0


def test_currency_ableitung_aus_einem_money():
    """ADR-013: ein parse_currency-Ergebnis liefert sowohl price als auch currency.

    price ist der Decimal-Betrag, currency wird via currency_from_symbol aus
    demselben Money abgeleitet — die zeilenweise Validierung sieht damit nie
    einen Nicht-Decimal-Preis.
    """
    money = parse_currency("£51.77")
    assert isinstance(money, Money)
    price = money.amount
    currency = currency_from_symbol(money)
    assert isinstance(price, Decimal)
    assert price == Decimal("51.77")
    assert currency == "GBP"


def test_currency_from_symbol_none_bleibt_none():
    assert currency_from_symbol(None) is None
