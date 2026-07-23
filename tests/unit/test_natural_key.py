"""Prueft den natural_key-Encoder (ADR-009 Kontrakt 2).

Der partielle Unique-Index ``record_current`` (docs/03 §Silver) macht die
Kodierung unumkehrbar: bilden zwei verschiedene Komponenten-Tupel auf denselben
String ab, verschmelzen zwei Entitaeten still. docs/02 §Feldsemantik ist
ausdruecklich, dass ein kaputter natural_key den Bestand rueckwirkend
korrumpiert — deshalb wirft der Encoder, statt einen Platzhalter zu erfinden.
"""

from __future__ import annotations

import pytest
from kintsugi.canonical import NaturalKeyMissing, encode_natural_key


def test_einzelne_komponente_bleibt_blanker_wert():
    """Damit gold_book den upc unveraendert ausgeben kann (docs/03 §Gold)."""
    key = encode_natural_key(["upc"], {"upc": "a897fe39b1053632"})
    assert key == "a897fe39b1053632"


def test_kollision_durch_trennzeichen_ist_ausgeschlossen():
    """['a|','b'] und ['a','|b'] duerfen nicht denselben String ergeben."""
    links = encode_natural_key(["a", "b"], {"a": "x|", "b": "y"})
    rechts = encode_natural_key(["a", "b"], {"a": "x", "b": "|y"})
    assert links != rechts


def test_kollision_ueber_das_escape_zeichen_ist_ausgeschlossen():
    """Ein Backslash im Wert darf keine Trennzeichen-Grenze vortaeuschen."""
    links = encode_natural_key(["a", "b"], {"a": "x\\", "b": "y"})
    rechts = encode_natural_key(["a", "b"], {"a": "x", "b": "\\y"})
    assert links != rechts


def test_einzelkomponente_mit_trennzeichen_bleibt_blank():
    """Bei einer Komponente gibt es nichts zu escapen — Wert bleibt unveraendert."""
    assert encode_natural_key(["k"], {"k": "a|b"}) == "a|b"


def test_separator_ist_kein_whitespace():
    """Sonst frisst str.strip() ihn vor dem Escapen und Werte kollidieren."""
    from kintsugi.canonical import _NK_SEPARATOR

    assert not _NK_SEPARATOR.isspace()


def test_reihenfolge_der_komponenten_ist_teil_der_identitaet():
    vorwaerts = encode_natural_key(["a", "b"], {"a": "1", "b": "2"})
    rueckwaerts = encode_natural_key(["b", "a"], {"a": "1", "b": "2"})
    assert vorwaerts != rueckwaerts


@pytest.mark.parametrize(
    "values",
    [
        {},  # Komponente fehlt ganz
        {"upc": None},  # None
        {"upc": "   "},  # nur Whitespace
        {"upc": ""},  # leer
    ],
)
def test_fehlende_komponente_wirft_statt_platzhalter(values):
    with pytest.raises(NaturalKeyMissing):
        encode_natural_key(["upc"], values)


def test_nichtstring_wird_zu_string_normalisiert():
    """Eine extrahierte Zahl darf einen gueltigen Schluessel ergeben."""
    assert encode_natural_key(["id"], {"id": 12345}) == "12345"


def test_whitespace_wird_getrimmt():
    assert encode_natural_key(["upc"], {"upc": "  a897fe39b1053632  "}) == "a897fe39b1053632"


def test_mehrere_komponenten_werden_verbunden():
    key = encode_natural_key(["domain", "id"], {"domain": "books", "id": "42"})
    assert "books" in key
    assert "42" in key
    assert key != "books42"  # Trennzeichen liegt dazwischen
