"""Soft-404- und Block-Erkennung teilen eine Signaturliste (I1.4.2, N01/N02, F1).

Die Vertragspruefung: Fetcher-Vorpruefung (Phase 1) und Heiler-Vorpruefung
(Phase 2) duerfen sich nie darueber uneinig sein, was „blockiert" heisst. Der
Import-Graph-Test unten macht das strukturell fest — genau ein Definitionsort je
Detektor, und nur die Fetch-Schicht oeffnet signatures.yaml.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest
from kintsugi.fetch.block_detect import (
    detect_block,
    detect_soft_404,
    load_signature_file,
    resolve_soft_404_signatures,
)
from kintsugi.packs.model import SitePack
from pydantic import ValidationError

_REPO = pathlib.Path(__file__).resolve().parents[2]
_KINTSUGI = _REPO / "kintsugi"

# --- Gespeicherte Fixtures -------------------------------------------------

# N01: Consent-Wall mit HTTP 200 (der Statuscode verraet nichts).
N01_CONSENT_WALL = (
    "<html><head><title>Bitte zustimmen</title></head><body>"
    '<div id="onetrust-consent-sdk">Wir verwenden Cookies.</div>'
    "</body></html>"
)

# N02: Soft-404 — Status 200, aber der Inhalt ist eine Fehlerseite.
N02_SOFT_404 = (
    "<html><head><title>404 Page Not Found</title></head><body>"
    "<h1>Oops!</h1><p>The page you requested could not be found.</p>"
    "</body></html>"
)

# F1: der *echte* HTTP-404-Koerper von books.toscrape.com/catalogue/page-51.html.
# Er sieht aus wie eine Fehlerseite — genau deshalb ist der Statuscheck noetig.
F1_REAL_404 = (
    "<html><head><title>404 Not Found</title></head><body>"
    "<h1>Not found</h1><p>The requested URL was not found on this server.</p>"
    "</body></html>"
)


def test_n01_consent_wall_status_200():
    hit = detect_block(N01_CONSENT_WALL, headers={})
    assert hit is not None
    assert hit.id == "onetrust_cmp"


def test_n02_soft_404_status_200():
    hit = detect_soft_404(N02_SOFT_404, http_status=200, url="https://example.test/x")
    assert hit is not None
    assert hit.id == "soft_404_title"


def test_f1_echter_404_ist_kein_soft_404():
    # Nicht-200-Status: immer None, egal wie sehr der Koerper nach 404 aussieht.
    assert detect_soft_404(F1_REAL_404, http_status=404, url="https://x/page-51.html") is None
    # Derselbe Koerper mit Status 200 waere ein Soft-404 — beweist: der Status
    # kurzschliesst, nicht der Inhalt.
    hit = detect_soft_404(F1_REAL_404, http_status=200, url="https://x/page-51.html")
    assert hit is not None
    assert hit.id == "soft_404_title"


# --- Loader-Vertrag --------------------------------------------------------


def test_loader_lehnt_fehlendes_schema_version_ab(tmp_path):
    bad = tmp_path / "sig.yaml"
    bad.write_text(
        textwrap.dedent("""
        block_signatures: []
        soft_404_signatures: []
        """),
        encoding="utf-8",
    )
    load_signature_file.cache_clear()
    with pytest.raises(ValidationError, match="schema_version"):
        load_signature_file(str(bad))
    load_signature_file.cache_clear()


def test_loader_lehnt_doppelte_id_ueber_beide_listen_ab(tmp_path):
    bad = tmp_path / "sig.yaml"
    bad.write_text(
        textwrap.dedent("""
        schema_version: 1
        block_signatures:
          - {id: shared, pattern: a, scope: body, kind: regex, source_note: x}
        soft_404_signatures:
          - {id: shared, pattern: b, scope: body, kind: regex, source_note: y}
        """),
        encoding="utf-8",
    )
    load_signature_file.cache_clear()
    with pytest.raises(ValidationError, match="Doppelte Signatur-id"):
        load_signature_file(str(bad))
    load_signature_file.cache_clear()


# --- Pack-Override ---------------------------------------------------------

_COMPLIANCE = {
    "tos_url": "https://example.test/",
    "tos_verdict": "permits",
    "tos_reviewed_at": "2026-07-21",
    "reviewed_by": "human:sven",
    "robots_checked_at": "2026-07-21",
    "public_content": True,
    "personal_data": False,
}


def _pack_with_soft_404_override() -> SitePack:
    return SitePack.model_validate(
        {
            "apiVersion": "kintsugi/v1",
            "domain": "example.test",
            "entity": "thing",
            "version": 1,
            "discovery": {"strategy": "pagination", "url_template": "p-{n}.html"},
            "fetch": {
                "soft_404_signatures": {
                    "signatures": [
                        {
                            "id": "acme_soft_404",
                            "pattern": ".acme-not-found",
                            "scope": "body",
                            "kind": "css",
                            "source_note": "Acme theme 404 container",
                        }
                    ]
                }
            },
            "extract": {"sources": [{"kind": "css", "fields": {"title": {"selector": "h1"}}}]},
            "schema": {
                "natural_key": ["upc"],
                "fields": {"upc": {"type": "string", "required": True}},
            },
            "compliance": dict(_COMPLIANCE),
        }
    )


def test_pack_override_feuert_nur_fuer_dieses_pack():
    # Ein Koerper, der KEINE globale Soft-404-Signatur trifft, aber die Pack-Signatur.
    body = (
        "<html><head><title>Acme Shop</title></head><body>"
        '<div class="acme-not-found">Leider verlegt.</div></body></html>'
    )
    pack = _pack_with_soft_404_override()

    # Default-Liste: kein Treffer.
    assert detect_soft_404(body, http_status=200, url="https://example.test/x") is None

    # Pack-Liste (global + Override): der Pack-Eintrag feuert.
    resolved = resolve_soft_404_signatures(pack.fetch.soft_404_signatures)
    hit = detect_soft_404(body, http_status=200, url="https://example.test/x", signatures=resolved)
    assert hit is not None
    assert hit.id == "acme_soft_404"


# --- Import-Graph: eine Wahrheit ueber „blockiert" -------------------------


def _py_files() -> list[pathlib.Path]:
    return list(_KINTSUGI.rglob("*.py"))


@pytest.mark.parametrize("marker", ["def detect_block(", "def detect_soft_404("])
def test_genau_ein_definitionsort_je_detektor(marker):
    hits = [p for p in _py_files() if marker in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, f"{marker} sollte genau einmal definiert sein, gefunden in {hits}"


def test_nur_die_fetch_schicht_oeffnet_signatures_yaml():
    fetch_dir = _KINTSUGI / "fetch"
    offenders = [
        p
        for p in _py_files()
        if "signatures.yaml" in p.read_text(encoding="utf-8") and fetch_dir not in p.parents
    ]
    assert offenders == [], (
        f"signatures.yaml wird ausserhalb von kintsugi/fetch geoeffnet: {offenders}"
    )
