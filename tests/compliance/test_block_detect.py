"""Prueft die Block-Erkennung per Body-Signatur (I0.7.7 / I1.4.2, N01)."""

from __future__ import annotations

import textwrap

import pytest
from kintsugi.fetch.block_detect import (
    SignatureEntry,
    block_signatures,
    detect_block,
    load_signature_file,
)
from pydantic import ValidationError

# Eine Consent-Wall mit HTTP 200 — der Statuscode verraet nichts.
CONSENT_WALL = (
    "<html><body>"
    '<div id="onetrust-consent-sdk">Wir verwenden Cookies. Bitte zustimmen.</div>'
    "</body></html>"
)

# Eine echte, harmlose Produktseite (gekuerzt), deutlich ueber dem Text-Floor.
PRODUCT_PAGE = (
    "<html><body><div class='product_main'><h1>A Light in the Attic</h1>"
    "<p class='price_color'>£51.77</p>"
    "<p class='availability'>In stock (22 available)</p>"
    "<p>Es war einmal ein Buch mit einem sehr langen Beschreibungstext, der weit "
    "ueber zweihundert Bytes hinausgeht, damit der Text-Floor nicht anschlaegt und "
    "die Seite als legitim erkannt wird.</p></div></body></html>"
)


def test_consent_wall_mit_status_200_wird_erkannt():
    hit = detect_block(CONSENT_WALL, headers={})
    assert hit is not None
    assert hit.id == "onetrust_cmp"


def test_echte_produktseite_wird_nicht_geflaggt():
    assert detect_block(PRODUCT_PAGE, headers={}) is None


def test_cloudflare_just_a_moment():
    body = "<html><body>Just a moment...</body></html>"
    hit = detect_block(body, headers={})
    assert hit is not None
    assert hit.id == "cloudflare_just_a_moment"


def test_cf_mitigated_header():
    hit = detect_block("<html><body>egal</body></html>", headers={"cf-mitigated": "challenge"})
    assert hit is not None
    assert hit.id == "cf_mitigated_header"


def test_reference_id_regex():
    body = "<html><body>Access denied. Reference #18.abc</body></html>"
    hit = detect_block(body, headers={})
    assert hit is not None
    assert hit.id == "reference_id"


def test_meta_refresh_auf_consent():
    body = (
        '<html><head><meta http-equiv="refresh" content="0; url=/consent">'
        "</head><body>x</body></html>"
    )
    hit = detect_block(body, headers={})
    assert hit is not None
    assert hit.id == "meta_refresh_consent"


def test_leere_seite_unter_dem_text_floor():
    hit = detect_block("<html><body><p>zu kurz</p></body></html>", headers={})
    assert hit is not None
    assert hit.id == "empty_below_text_floor"


def test_kurze_legitime_seite_ueber_dem_floor_ist_ok():
    body = (
        "<html><body><p>" + "Eine kurze, aber legitime Seite mit genug sichtbarem Text ueber der "
        "Zweihundert-Byte-Grenze, damit der Floor nicht faelschlich anschlaegt. "
        * 2
        + "</p></body></html>"
    )
    assert detect_block(body, headers={}) is None


def test_loader_lehnt_doppelte_ids_ab(tmp_path):
    bad = tmp_path / "sig.yaml"
    bad.write_text(
        textwrap.dedent("""
        schema_version: 1
        block_signatures:
          - {id: dup, pattern: a, scope: body, kind: regex, source_note: x}
          - {id: dup, pattern: b, scope: body, kind: regex, source_note: y}
        soft_404_signatures: []
        """),
        encoding="utf-8",
    )
    load_signature_file.cache_clear()
    with pytest.raises(ValidationError, match="Doppelte Signatur-id"):
        load_signature_file(str(bad))
    load_signature_file.cache_clear()


def test_jede_signatur_hat_id_und_source_note():
    for sig in block_signatures():
        assert isinstance(sig, SignatureEntry)
        assert sig.id
        assert sig.source_note
