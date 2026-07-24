"""Prueft den CssExtractor und den Canary gegen die Baseline-Fixture (I0.8.2)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import selectolax
from kintsugi.extract.css import CssExtractor
from kintsugi.packs.model import CssSource
from selectolax.lexbor import LexborHTMLParser

FIXTURE_DIR = Path("fixtures/books.toscrape.com/book/golden/baseline")
FIXTURE = FIXTURE_DIR / "page.html.gz"

BOOK_SOURCE = CssSource.model_validate(
    {
        "kind": "css",
        "fields": {
            "title": {"selector": "div.product_main > h1"},
            "price": {"selector": "p.price_color"},
            "availability": {"selector": "p.availability"},
            "upc": {"selector": "table.table-striped tr:nth-child(1) td"},
        },
    }
)


def _doc() -> LexborHTMLParser:
    return LexborHTMLParser(gzip.decompress(FIXTURE.read_bytes()).decode("utf-8"))


def test_selectolax_ist_exakt_gepinnt():
    assert selectolax.__version__ == "0.4.11"


def test_canary_vier_werte_byte_fuer_byte():
    """F2: die vier live verifizierten Selektoren, exakt."""
    row = CssExtractor().extract(_doc(), BOOK_SOURCE)
    assert row["title"] == "A Light in the Attic"
    assert row["price"] == "£51.77"  # £51.77
    assert row["availability"] == "In stock (22 available)"
    assert row["upc"] == "a897fe39b1053632"


def test_nth_child_loest_unter_lexbor_auf():
    """Der natural_key haengt an :nth-child; lexbor muss es koennen."""
    node = _doc().css_first("table.table-striped tr:nth-child(1) td")
    assert node is not None
    assert node.text(strip=True) == "a897fe39b1053632"


def test_meta_json_liegt_neben_der_fixture():
    meta = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["label"] == "baseline"
    assert meta["expected"]["upc"] == "a897fe39b1053632"


def test_fehltreffer_ist_none_ohne_ausnahme():
    source = CssSource.model_validate(
        {"kind": "css", "fields": {"gibtsnicht": {"selector": "div.existiert-nicht"}}}
    )
    row = CssExtractor().extract(_doc(), source)
    assert row["gibtsnicht"] is None


def test_row_selector_liefert_eine_zeile_je_knoten():
    html = """
    <ul>
      <li class="item"><span class="n">A</span></li>
      <li class="item"><span class="n">B</span></li>
      <li class="item"><span class="n">C</span></li>
    </ul>
    """
    source = CssSource.model_validate(
        {"kind": "css", "row_selector": "li.item", "fields": {"n": {"selector": "span.n"}}}
    )
    rows = CssExtractor().extract_all(LexborHTMLParser(html), source)
    assert [r["n"] for r in rows] == ["A", "B", "C"]


def test_attr_statt_text():
    html = '<html><body><a class="x" data-price="49.90">Kaufen</a></body></html>'
    source = CssSource.model_validate(
        {"kind": "css", "fields": {"price": {"selector": "a.x", "attr": "data-price"}}}
    )
    row = CssExtractor().extract(LexborHTMLParser(html), source)
    assert row["price"] == "49.90"
