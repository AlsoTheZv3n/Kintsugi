"""JSON-LD-Extraktor: Fixtures, Dokumentreihenfolge, kaputte Bloecke, Fehltreffer (I1.5.2b)."""

from __future__ import annotations

from pathlib import Path

import kintsugi.extract  # noqa: F401  # registriert css/embedded_json/jsonld
import pytest
from kintsugi.extract.base import resolve
from kintsugi.extract.chain import run_chain
from kintsugi.extract.jsonld import JsonLdExtractor
from kintsugi.packs.model import CssSource, EmbeddedJsonSource, FieldExtract, JsonLdSource
from selectolax.lexbor import LexborHTMLParser

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "_synthetic" / "jsonld"
_EX = JsonLdExtractor()
_PRODUCT = JsonLdSource(
    kind="jsonld", type="Product", fields={"name": "$.name", "price": "$.offers.price"}
)


def _doc(name: str) -> LexborHTMLParser:
    return LexborHTMLParser((_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("fixture", "name", "price"),
    [
        ("product.html", "Alpha", "9.99"),
        ("graph.html", "Beta", "1.50"),  # @graph-Wrapper
        ("array.html", "Gamma", "3.00"),  # Top-Level-Array
        ("two_products.html", "Delta", "4.00"),  # Dokumentreihenfolge: erster gewinnt
        ("malformed.html", "Zeta", "6.00"),  # kaputter Block daneben, wirft nichts
    ],
)
def test_extrahiert_je_fixture(fixture, name, price):
    result = _EX.extract(_doc(fixture), _PRODUCT)
    assert result["name"] == name
    assert result["price"] == price


def test_ohne_felder_map_die_rohen_keys():
    src = JsonLdSource(kind="jsonld", type="Product")
    result = _EX.extract(_doc("product.html"), src)
    assert result["@type"] == "Product"
    assert result["name"] == "Alpha"


def test_kein_treffer_ist_fehltreffer_und_kette_faellt_auf_css():
    doc = LexborHTMLParser("<html><body><h1>Titel X</h1></body></html>")
    # jsonld findet keinen Product -> {}
    assert _EX.extract(doc, _PRODUCT) == {}
    # Kette jsonld (miss) -> embedded_json (miss, script_id fehlt) -> css (Treffer).
    sources = [
        JsonLdSource(kind="jsonld", type="Product", fields={"name": "$.name"}),
        EmbeddedJsonSource(kind="embedded_json", script_id="__ABSENT__"),
        CssSource(kind="css", fields={"title": FieldExtract(selector="h1")}),
    ]
    values, provenance = run_chain(sources, doc)
    assert values["title"] == "Titel X"
    assert provenance["title"] == "css"


def test_stub_phasen_bleiben_fuer_api_und_llm():
    # jsonld/embedded_json sind gebaut; api/llm bleiben Stubs mit ihrer Phase.
    with pytest.raises(NotImplementedError, match="Phase 5"):
        resolve("api").extract(doc=None, source=None)
    with pytest.raises(NotImplementedError, match="Phase 4"):
        resolve("llm").extract(doc=None, source=None)
