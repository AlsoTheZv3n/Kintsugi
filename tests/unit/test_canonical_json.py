"""Nagelt die Kanonisierung und den payload_hash fest (ADR-009 Kontrakt 1).

Der Digest ist ein unumkehrbarer Vertrag: er ist der Deduplizierungsschluessel
im Silver-Bestand. Aendert sich die Kanonisierung unbemerkt, bekommen gleiche
Inhalte verschiedene Hashes und der Bestand spaltet sich still. Deshalb ein
festgeschriebener Golden-Digest plus Eigenschaftstests fuer die Invarianzen,
auf die sich der Vertrag beruft.
"""

from __future__ import annotations

import json
import unicodedata
from decimal import Decimal
from pathlib import Path

import pytest
from kintsugi.canonical import canonical_json, payload_hash

GOLDEN = Path(__file__).parent / "golden" / "payload_hash.json"

# Als Literal festgeschrieben, nicht aus der Golden-Datei berechnet: sonst
# waere der Test immer gruen. Aendert sich die Kanonisierung, muss BEIDES
# bewusst angefasst werden — dieser Wert und die Golden-Datei.
EXPECTED_HEX = "c5eeefaa2a1f3f8063301d647db8328691a79ea4e700391b769b659085e01bb3"


# --------------------------------------------------------------------------
# Golden-Digest
# --------------------------------------------------------------------------


def test_golden_digest_bleibt_stabil():
    """Bricht laut, sobald sich die Kanonisierung aendert."""
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    payload = dict(data["payload"])
    payload["price"] = Decimal(payload["price"])  # JSON kennt kein Decimal
    computed = payload_hash(payload).hex()
    assert computed == EXPECTED_HEX  # gegen das Literal, nicht die Datei
    assert data["sha256_hex"] == EXPECTED_HEX  # Datei und Literal muessen uebereinstimmen
    assert canonical_json(payload).decode("utf-8") == data["canonical"]


# --------------------------------------------------------------------------
# Invarianzen
# --------------------------------------------------------------------------


def test_schluesselreihenfolge_ist_egal():
    assert payload_hash({"a": 1, "b": 2}) == payload_hash({"b": 2, "a": 1})


def test_verschachtelte_schluesselreihenfolge_ist_egal():
    links = {"outer": {"x": 1, "y": 2}, "list": [{"p": 1, "q": 2}]}
    rechts = {"list": [{"q": 2, "p": 1}], "outer": {"y": 2, "x": 1}}
    assert payload_hash(links) == payload_hash(rechts)


def test_nfd_und_nfc_hashen_gleich():
    """Dieselbe Zeichenkette in zwei Unicode-Normalformen ist ein Wert."""
    nfc = unicodedata.normalize("NFC", "Café")
    nfd = unicodedata.normalize("NFD", "Café")
    assert nfc != nfd  # verschiedene Bytes
    assert payload_hash({"t": nfc}) == payload_hash({"t": nfd})


def test_decimal_aus_parse_currency_hasht_wie_direktes_decimal():
    """Runde ueber die Transform-Kette darf den Digest nicht bewegen."""
    direkt = {"price": Decimal("51.77")}
    ueber_string = {"price": Decimal("51.77".strip())}
    assert payload_hash(direkt) == payload_hash(ueber_string)


def test_geaenderter_wert_aendert_den_digest():
    assert payload_hash({"price": Decimal("51.77")}) != payload_hash({"price": Decimal("51.78")})


# --------------------------------------------------------------------------
# Decimal, float, None
# --------------------------------------------------------------------------


def test_decimal_wird_als_zahl_token_ausgegeben():
    out = canonical_json({"price": Decimal("51.77")})
    assert b'"price":51.77' in out
    assert b'"51.77"' not in out  # nicht als String
    assert b"51.7699" not in out  # kein float-Artefakt


def test_float_wird_abgelehnt():
    """float macht den Hash unreproduzierbar — Geldwerte muessen Decimal sein."""
    with pytest.raises(ValueError, match="float"):
        canonical_json({"price": 51.77})


def test_none_wird_abgelehnt():
    """Abwesende Felder werden weggelassen, nicht als null geschrieben."""
    with pytest.raises(ValueError, match="None"):
        canonical_json({"optional": None})


def test_abwesendes_feld_taucht_nicht_auf():
    mit = canonical_json({"a": 1, "b": 2})
    ohne = canonical_json({"a": 1})
    assert b'"b"' in mit
    assert b'"b"' not in ohne


def test_bool_kommt_vor_int():
    """bool ist Unterklasse von int; true/false, nicht 1/0."""
    out = canonical_json({"flag": True, "zahl": 1})
    assert b'"flag":true' in out
    assert b'"zahl":1' in out


def test_nicht_serialisierbarer_typ_wirft():
    with pytest.raises(TypeError):
        canonical_json({"x": object()})


def test_nicht_string_schluessel_wirft():
    with pytest.raises(TypeError):
        canonical_json({1: "x"})
