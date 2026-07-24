"""Prueft den ausgelieferten quotes.toscrape.com/js-Pack (I1.5.4, #104).

Offline gegen einen **synthetischen** /js/-Snapshot: die echten 30 Golden-Captures
sind #106 (die Suite ist netz-verriegelt). Der Snapshot traegt die live verifizierte
Nutzlast-Form -- ``var data = [...]`` mit verschachteltem ``author``-Objekt -- und
**keine** ``div.quote``-Elemente, damit die css-Fallback-Quelle wie auf der echten
/js/-Seite einen Fehltreffer liefert und die embedded_json-Quelle alle Zeilen traegt.

Kein DB-, kein Netzzugriff (nur ein geparster String), daher ohne
``integration``-Marker -- der Test laeuft im Standardlauf mit.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from kintsugi.extract.css import CssExtractor
from kintsugi.extract.entity import extract_entities
from kintsugi.harness.sources import PHASE1_SOURCES
from kintsugi.packs.loader import load_pack
from kintsugi.packs.validate import validate_pack
from selectolax.lexbor import LexborHTMLParser

PACKS_ROOT = Path("packs")


def _pack():
    return load_pack("quotes.toscrape.com", "quote", root=PACKS_ROOT)


# --- synthetischer /js/-Snapshot: 10 Zitate in `var data`, keine div.quote ------


def _quote(i: int) -> dict:
    return {
        "text": f"Zitat Nummer {i} ueber das Leben.",
        "author": {
            "name": f"Autor {i}",
            "goodreads_link": f"/author/autor-{i}",
            "slug": f"autor-{i}",
        },
        "tags": ["leben", "wahrheit"] if i % 2 == 0 else [],
    }


def _js_page(data: list[dict]) -> str:
    return (
        "<!DOCTYPE html><html><head><title>Quotes to Scrape</title></head><body>"
        "<div class='container'><div class='row'><div class='col-md-8'>"
        f"<script>var data = {json.dumps(data)};</script>"
        "</div></div></div></body></html>"
    )


def _doc(data: list[dict] | None = None) -> LexborHTMLParser:
    return LexborHTMLParser(_js_page(data if data is not None else [_quote(i) for i in range(10)]))


def _quote_id(text: str, slug: str, name: str = "Egal") -> str:
    """quote_id einer einzelnen, durch den Pack extrahierten Entitaet."""
    data = [{"text": text, "author": {"name": name, "slug": slug}, "tags": []}]
    (entity,) = extract_entities(_pack(), _doc(data))
    values, _ = entity
    return str(values["quote_id"])


# --- AC1: validate_pack ---------------------------------------------------------


def test_pack_besteht_die_statischen_pruefungen():
    errors = [f for f in validate_pack(_pack()) if f.severity == "error"]
    assert errors == [], f"unerwartete Fehler: {errors}"


def test_jedes_schema_feld_hat_eine_quelle():
    # AC1 explizit: die ADR-013-Regel (kein field_without_source-Finding).
    findings = validate_pack(_pack())
    assert [f for f in findings if f.check_id == "field_without_source"] == []


# --- AC2: 10 Zitate mit erwarteten Feldtypen ------------------------------------


def test_liefert_genau_zehn_zitate_mit_erwarteten_feldern():
    entities = extract_entities(_pack(), _doc())
    assert len(entities) == 10
    for values, provenance in entities:
        assert values["text"]  # non-empty text
        assert isinstance(values["author"], str)  # aus $.author.name (String, nicht Objekt)
        assert isinstance(values["author_slug"], str)
        assert isinstance(values["tags"], list)  # tags als Liste
        assert provenance["text"] == "embedded_json"  # Quelle 1 gewinnt
        assert re.fullmatch(r"[a-f0-9]{16}", str(values["quote_id"]))


def test_css_fallback_ist_auf_js_ein_fehltreffer():
    # Auf /js/ keine div.quote -> die css-Quelle liefert 0 Zeilen; alle 10 Zeilen
    # kommen aus embedded_json (die Provenance im Test oben beweist die Herkunft).
    css_source = _pack().extract.sources[1]
    assert css_source.kind == "css"
    assert CssExtractor().extract_all(_doc(), css_source) == []


# --- AC3: /js/ mit abschliessendem Slash ---------------------------------------


def test_eintritts_url_und_template_tragen_den_schraegstrich():
    pack = _pack()
    assert "/js/" in pack.discovery.url_template
    entry = PHASE1_SOURCES["quotes_js"].entry_url
    assert entry == "https://quotes.toscrape.com/js/"
    # /js OHNE Slash ist nicht, was der Pack deklariert.
    assert not entry.endswith("/js")


# --- AC4: Natural-Key-Stabilitaet ----------------------------------------------


def test_natural_key_stabil_ueber_nfc_nfd_und_whitespace():
    base = _quote_id("café sagt etwas", "autor-x")
    nfd = _quote_id(unicodedata.normalize("NFD", "café sagt etwas"), "autor-x")
    whitespace = _quote_id("   café   sagt etwas  ", "autor-x")
    assert base == nfd == whitespace


def test_verschiedene_zitate_desselben_autors_ergeben_verschiedene_ids():
    assert _quote_id("erstes Zitat", "autor-x") != _quote_id("zweites Zitat", "autor-x")


def test_natural_key_aus_slug_nicht_anzeigename():
    # Der Anzeigename geht nicht in den Schluessel ein (source: [author_slug, text]).
    assert _quote_id("café sagt etwas", "autor-x", name="Ein Name") == _quote_id(
        "café sagt etwas", "autor-x", name="Ganz Anderer Name"
    )


# --- AC5: fetch.strategy http, kein Browser, provisional, Compliance -----------


def test_pack_deklariert_http_ohne_browser_und_provisional():
    pack = _pack()
    assert pack.fetch.strategy == "http"
    assert pack.fetch.browser is None
    assert pack.quality.thresholds_source == "provisional"
    assert pack.compliance.tos_verdict == "permits"
    assert pack.compliance.reviewed_by
