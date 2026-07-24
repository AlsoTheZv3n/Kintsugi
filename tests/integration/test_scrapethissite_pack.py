"""Prueft den ausgelieferten scrapethissite.com/oscar_film-Pack (I1.5.5, #105).

Offline: der xhr-Pfad laeuft gegen eine **Attrappe** des Fetchers mit der roh
committeten AJAX-JSON-Antwort, der css-Fallback gegen zwei synthetische HTML-
Fixtures (die leere AJAX-Tabelle und eine /pages/simple/-artige Laenderliste).
Die 30 Live-Golden-Captures sind #106. Kein DB-, kein Netzzugriff -> ohne
``integration``-Marker, laeuft im Standardlauf mit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from kintsugi.extract.css import CssExtractor
from kintsugi.extract.derive import apply_derived_fields
from kintsugi.extract.xhr import XhrExtractor
from kintsugi.fetch.base import FetchOutcome, FetchResult
from kintsugi.packs.loader import load_pack
from kintsugi.packs.validate import validate_pack
from kintsugi.validate.dynamic_model import validate_row
from selectolax.lexbor import LexborHTMLParser

PACKS_ROOT = Path("packs")
PACK_FILE = PACKS_ROOT / "scrapethissite.com" / "oscar_film.yaml"


def _pack():
    return load_pack("scrapethissite.com", "oscar_film", root=PACKS_ROOT)


# --- Fixtures ------------------------------------------------------------------

# Der AJAX-Endpunkt liefert ein bares Array; title traegt Rand-Whitespace, und
# best_picture fehlt auf der Nicht-Gewinner-Zeile.
_AJAX_FILMS = json.dumps(
    [
        {"title": "Spotlight  ", "year": 2015, "awards": 2, "nominations": 6, "best_picture": True},
        {"title": "Mad Max: Fury Road", "year": 2015, "awards": 6, "nominations": 10},
    ]
)

# Server-HTML der AJAX-Seite: <tbody id="table-body"> LEER, kein tr.country.
_AJAX_HTML = (
    "<html><body><table id='oscars'>"
    "<thead><tr><th>Title</th><th>Year</th></tr></thead>"
    "<tbody id='table-body'></tbody></table></body></html>"
)

# /pages/simple/-artige, server-gerenderte Laenderliste mit tr.country-Zeilen.
_SIMPLE_HTML = (
    "<html><body><table>"
    "<tr class='country'><td><h3 class='country-name'>"
    "<i class='flag'></i>  Andorra  </h3></td></tr>"
    "<tr class='country'><td><h3 class='country-name'>Bhutan</h3></td></tr>"
    "</table></body></html>"
)


class _FakeFetcher:
    def __init__(self, result: FetchResult) -> None:
        self._result = result
        self.calls: list[tuple[str, object]] = []

    def fetch(self, url, *, etag=None, last_modified=None, headers=None):
        self.calls.append((url, headers))
        return self._result


def _json_result(body: str) -> FetchResult:
    return FetchResult(
        url="https://www.scrapethissite.com/pages/ajax-javascript/",
        final_url="https://www.scrapethissite.com/pages/ajax-javascript/",
        http_status=200,
        headers={},
        body=body.encode("utf-8"),
        content_type="application/json",
        encoding="utf-8",
        elapsed_ms=2,
        fetcher="httpx",
        from_cache=False,
        outcome=FetchOutcome.ok,
    )


def _xhr_rows() -> list[dict[str, object]]:
    xhr = _pack().extract.sources[0]
    fetcher = _FakeFetcher(_json_result(_AJAX_FILMS))
    return XhrExtractor(fetcher).extract_all(
        xhr, page_url="https://www.scrapethissite.com/pages/ajax-javascript/?year=2015"
    )


# --- AC1: validate_pack + Quellen-Reihenfolge ---------------------------------


def test_pack_besteht_die_statischen_pruefungen():
    errors = [f for f in validate_pack(_pack()) if f.severity == "error"]
    assert errors == [], f"unerwartete Fehler: {errors}"


def test_quellen_reihenfolge_ist_xhr_dann_css_mit_row_root():
    pack = _pack()
    assert [s.kind for s in pack.extract.sources] == ["xhr", "css"]
    assert pack.extract.sources[0].row_root == "$[*]"


# --- AC2: css-Fallback -- Fehltreffer auf AJAX, Treffer auf /pages/simple/ -----


def test_css_fehltreffer_auf_ajax_treffer_auf_simple():
    css = _pack().extract.sources[1]
    # Leeres tbody, kein tr.country -> typisierter Fehltreffer (leere Liste).
    assert CssExtractor().extract_all(LexborHTMLParser(_AJAX_HTML), css) == []
    # /pages/simple/: echte Zeilen (title gestrippt).
    rows = CssExtractor().extract_all(LexborHTMLParser(_SIMPLE_HTML), css)
    assert [r["title"] for r in rows] == ["Andorra", "Bhutan"]


# --- AC3: title gestrippt, best_picture optional ------------------------------


def test_title_gestrippt_und_best_picture_optional_ohne_validierungsfehler():
    pack = _pack()
    rows = _xhr_rows()
    # Der Endpunkt liefert "Spotlight  "; die strip-Transform macht "Spotlight".
    assert rows[0]["title"] == "Spotlight"
    # best_picture fehlt auf mindestens einer Zeile (Nicht-Gewinner).
    assert any("best_picture" not in row for row in rows)
    # Jede Zeile validiert -- best_picture required:false ist ein Fill-Rate-Miss,
    # kein harter Reject.
    for row in rows:
        result = validate_row(pack, apply_derived_fields(row, pack.schema_.fields))
        assert result.accepted, result.reasons


# --- AC4: abgeleiteter Natural Key, byte-identisch ----------------------------


def test_film_id_abgeleitet_stabil_und_aus_title_year():
    pack = _pack()
    ids1 = [apply_derived_fields(r, pack.schema_.fields)["film_id"] for r in _xhr_rows()]
    ids2 = [apply_derived_fields(r, pack.schema_.fields)["film_id"] for r in _xhr_rows()]
    assert ids1 == ids2  # byte-identisch ueber zwei Extraktionen
    assert all(re.fullmatch(r"[a-f0-9]{16}", str(i)) for i in ids1)
    assert len(set(ids1)) == len(ids1)  # verschiedene (title, year) -> verschiedene ids
    # Der Schluessel wird aus [title, year] gebaut -- den Feldern, die M15 entfernt.
    df = pack.schema_.fields["film_id"].derived_from
    assert df is not None
    assert df.source == ["title", "year"]
    assert df.transform == "natural_key_16"


# --- AC5: Compliance-Block ----------------------------------------------------


def test_compliance_nennt_robots_verdikt_und_grund():
    pack = _pack()
    assert pack.compliance.tos_verdict == "permits"
    text = PACK_FILE.read_text(encoding="utf-8")
    assert "/lessons/" in text
    assert "/faq/" in text
    assert "webscraper.io" in text  # der F4-Grund
    assert str(pack.compliance.robots_checked_at) in text
