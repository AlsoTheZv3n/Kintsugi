"""Prueft die Transform-Registry und die Kettenvalidierung (I0.5.1)."""

from __future__ import annotations

from kintsugi.transform import primitives  # noqa: F401  -- registriert die Primitiven
from kintsugi.transform.primitives import Money
from kintsugi.transform.registry import resolve, validate_chain


def test_parse_currency_deklariert_seine_typen():
    t = resolve("parse_currency")
    assert t is not None
    assert t.in_type is str
    # ADR-013: derived_from statt Multi-Output — parse_currency liefert einen
    # einzelnen Money-Wert.
    assert t.out_type is Money


def test_vertraegliche_kette_ist_leer():
    assert validate_chain(["strip", "parse_currency"]) == []


def test_typunvertraegliche_kette_meldet_genau_einen_befund():
    findings = validate_chain(["int_from_text", "parse_currency"])
    assert len(findings) == 1
    assert findings[0].code == "transform_type_mismatch"
    assert findings[0].position == 1


def test_unbekannter_transform_meldet_ohne_ausnahme():
    findings = validate_chain(["uppercase"])
    assert len(findings) == 1
    assert findings[0].code == "unknown_transform"


def test_leere_kette_ist_gueltig():
    assert validate_chain([]) == []


def test_registry_enthaelt_die_kern_primitiven():
    for name in ("strip", "nfc", "parse_currency", "int_from_text"):
        assert resolve(name) is not None


def test_jeder_transform_hat_einen_failure_mode():
    for name in ("strip", "nfc", "parse_currency", "int_from_text", "currency_from_symbol"):
        assert resolve(name).failure_mode.strip()
