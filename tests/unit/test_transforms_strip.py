"""Prueft strip und nfc (I0.5.2)."""

from __future__ import annotations

import unicodedata

from kintsugi.transform.primitives import nfc, strip
from kintsugi.transform.registry import resolve

# Sonderzeichen ueber chr(), damit der Quelltext ASCII bleibt und ruff nicht
# ueber mehrdeutige Unicode-Zeichen stolpert.
NBSP = chr(0x00A0)
NARROW_NBSP = chr(0x202F)
E_ACUTE = chr(0x00E9)  # é, vorkomponiert


def test_strip_kollabiert_whitespace():
    assert strip("  A Light in\n  the   Attic ") == "A Light in the Attic"


def test_strip_entschaerft_html_entities():
    assert strip("Tipping the Velvet &amp; More") == "Tipping the Velvet & More"


def test_strip_none_bleibt_none():
    assert strip(None) is None


def test_strip_leeres_ergebnis_wird_none():
    """Ein Leerstring erfuellte still required=true und blaehte die Fill-Rate."""
    assert strip("   ") is None


def test_strip_ersetzt_geschuetzte_leerzeichen():
    assert strip(f"A{NBSP}B{NARROW_NBSP}C") == "A B C"


def test_nfc_komponiert():
    zerlegt = unicodedata.normalize("NFD", E_ACUTE)
    assert nfc(zerlegt) == E_ACUTE
    assert nfc(E_ACUTE) == E_ACUTE


def test_nfc_ist_idempotent():
    x = unicodedata.normalize("NFD", f"Caf{E_ACUTE}")
    assert nfc(nfc(x)) == nfc(x)


def test_nfc_nach_strip_ist_noop():
    """NFKC-Ausgabe von strip ist bereits NFC-komponiert."""
    for x in (f"Caf{E_ACUTE}", E_ACUTE, "  A O  ", "naive"):
        assert nfc(strip(x)) == strip(x)


def test_nfc_none_bleibt_none():
    assert nfc(None) is None


def test_registry_typen_von_strip_und_nfc():
    for name in ("strip", "nfc"):
        t = resolve(name)
        assert t is not None
        assert t.in_type == (str | None)
        assert t.out_type == (str | None)
