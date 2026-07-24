"""extract_entities: Mehrzeilen-Naht — Ausrichtung, Fallback, Transform/derive je Zeile.

Die Einzeilen-Sicht (``extract_entity``) bleibt der N=1-Spezialfall; die Tests
verankern beide an derselben ``_assemble``-Mechanik. Die Ausrichtungs-Semantik —
„die erste Quelle mit >=1 Zeile setzt die Zeilenmenge, spaetere Quellen fuellen
positionsweise nur leere Felder" — ist die tragende Design-Entscheidung fuer
#104 (embedded_json, dann css) und #105 (xhr, dann css).
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from kintsugi.extract.entity import extract_entities, extract_entity
from kintsugi.packs.model import CssSource, FieldExtract, FieldSchema
from selectolax.lexbor import LexborHTMLParser


def _pack(sources: list[object], fields: dict[str, FieldSchema] | None = None) -> object:
    """Ein Minimal-Pack-Stellvertreter: nur was extract_entities liest."""
    return SimpleNamespace(
        extract=SimpleNamespace(sources=sources),
        schema_=SimpleNamespace(fields=fields or {}),
    )


class _FixedRows:
    """Test-Extraktor mit fest gesetzten Zeilen (fuer eingespeiste Registry)."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def extract(self, doc: object, source: object) -> dict[str, object]:
        return dict(self._rows[0]) if self._rows else {}

    def extract_all(self, doc: object, source: object) -> list[dict[str, object]]:
        return [dict(row) for row in self._rows]


# --------------------------------------------------------------------------
# Echte Extraktoren (globale Registry): N=1-Aequivalenz und row_selector-Fan-out
# --------------------------------------------------------------------------


def test_einzelquelle_n1_entspricht_extract_entity():
    # Eine css-Quelle ohne row_selector = eine Entitaet je Seite (books-Fall).
    source = CssSource(kind="css", fields={"title": FieldExtract(selector="h1")})
    pack = _pack([source])
    doc = LexborHTMLParser("<html><body><h1>Hallo</h1></body></html>")

    entities = extract_entities(pack, doc)  # type: ignore[arg-type]
    assert entities == [extract_entity(pack, doc)]  # type: ignore[arg-type]
    assert entities == [({"title": "Hallo"}, {"title": "css"})]


def test_row_selector_faechert_zu_n_entitaeten_auf():
    source = CssSource(
        kind="css", row_selector="li.item", fields={"n": FieldExtract(selector="span")}
    )
    pack = _pack([source])
    html = (
        "<ul>"
        '<li class="item"><span>A</span></li>'
        '<li class="item"><span>B</span></li>'
        '<li class="item"><span>C</span></li>'
        "</ul>"
    )
    doc = LexborHTMLParser(html)

    entities = extract_entities(pack, doc)  # type: ignore[arg-type]
    assert [values["n"] for values, _ in entities] == ["A", "B", "C"]
    # Die Einzeilen-Sicht liefert die erste Entitaet der Prioritaetskette.
    assert extract_entity(pack, doc)[0]["n"] == "A"  # type: ignore[arg-type]


def test_leere_listenseite_liefert_keine_entitaet():
    # row_selector gesetzt, kein Treffer -> 0 Entitaeten (nicht eine leere).
    source = CssSource(
        kind="css", row_selector="li.item", fields={"n": FieldExtract(selector="span")}
    )
    pack = _pack([source])
    doc = LexborHTMLParser("<html><body><p>nichts</p></body></html>")

    assert extract_entities(pack, doc) == []  # type: ignore[arg-type]
    # extract (Einzeilen) liefert im selben Fall bewusst eine leere Entitaet
    # (all-None) — der Unterschied ist der Kern der Mehrzeilen-Semantik.
    values, _ = extract_entity(pack, doc)  # type: ignore[arg-type]
    assert values == {"n": None}


# --------------------------------------------------------------------------
# Ausrichtungs-Semantik ueber Quellen (eingespeiste Registry)
# --------------------------------------------------------------------------


def test_primaerquelle_setzt_die_zeilenzahl():
    primary = SimpleNamespace(kind="primary")
    fallback = SimpleNamespace(kind="fallback")
    pack = _pack([primary, fallback])
    registry = {
        "primary": _FixedRows([{"a": "1"}, {"a": "2"}, {"a": "3"}]),
        "fallback": _FixedRows([{"a": "spät"}]),  # kuerzer und spaeter -> ignoriert
    }

    entities = extract_entities(pack, doc=None, registry=registry)  # type: ignore[arg-type]
    assert [values["a"] for values, _ in entities] == ["1", "2", "3"]
    # Die (kuerzere, nachrangige) Fallbackquelle verlaengert nicht und ueberschreibt
    # kein bereits gefuelltes Feld: Provenance ist ueberall die Primaerquelle.
    assert all(prov["a"] == "primary" for _, prov in entities)


def test_fallback_fuellt_luecken_positionsweise():
    primary = SimpleNamespace(kind="primary")
    fallback = SimpleNamespace(kind="fallback")
    pack = _pack([primary, fallback])
    registry = {
        "primary": _FixedRows([{"a": "x", "b": None}, {"a": "y", "b": None}]),
        "fallback": _FixedRows([{"b": "B0"}, {"b": "B1"}]),
    }

    entities = extract_entities(pack, doc=None, registry=registry)  # type: ignore[arg-type]
    values0, prov0 = entities[0]
    values1, prov1 = entities[1]
    assert values0 == {"a": "x", "b": "B0"}
    assert values1 == {"a": "y", "b": "B1"}
    # b kam positionsweise aus dem Fallback, a aus der Primaerquelle.
    assert prov0 == {"a": "primary", "b": "fallback"}
    assert prov1 == {"a": "primary", "b": "fallback"}


def test_fallback_uebernimmt_wenn_primaer_leer():
    primary = SimpleNamespace(kind="primary")
    fallback = SimpleNamespace(kind="fallback")
    pack = _pack([primary, fallback])
    registry = {
        "primary": _FixedRows([]),  # Fehltreffer (z. B. css auf /js/)
        "fallback": _FixedRows([{"a": "1"}, {"a": "2"}]),
    }

    entities = extract_entities(pack, doc=None, registry=registry)  # type: ignore[arg-type]
    assert [values["a"] for values, _ in entities] == ["1", "2"]
    assert all(prov["a"] == "fallback" for _, prov in entities)


def test_keine_quelle_liefert_zeilen():
    a = SimpleNamespace(kind="a")
    b = SimpleNamespace(kind="b")
    pack = _pack([a, b])
    registry = {"a": _FixedRows([]), "b": _FixedRows([])}

    assert extract_entities(pack, doc=None, registry=registry) == []  # type: ignore[arg-type]


def test_transform_und_derive_je_zeile():
    # Jede Zeile durchlaeuft die Feld-Transform-Kette UND derived_from.
    source = SimpleNamespace(
        kind="css",
        fields={"price": FieldExtract(selector="ignored", transform=["strip", "parse_currency"])},
    )
    schema_fields = {
        "price": FieldSchema(type="decimal", required=True),
        "currency": FieldSchema.model_validate(
            {
                "type": "string",
                "required": True,
                "derived_from": {"source": "price", "transform": "currency_from_symbol"},
            }
        ),
    }
    pack = _pack([source], schema_fields)
    registry = {"css": _FixedRows([{"price": " £5.00 "}, {"price": " £6.00 "}])}

    entities = extract_entities(pack, doc=None, registry=registry)  # type: ignore[arg-type]
    values0, _ = entities[0]
    values1, _ = entities[1]
    assert values0 == {"price": Decimal("5.00"), "currency": "GBP"}
    assert values1 == {"price": Decimal("6.00"), "currency": "GBP"}
