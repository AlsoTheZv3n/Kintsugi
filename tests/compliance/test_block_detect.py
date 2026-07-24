"""Prueft die Block-Erkennung per Body-Signatur (I0.7.7, N01)."""

from __future__ import annotations

import textwrap

import pytest
from kintsugi.fetch.block_detect import Signature, detect, load_signatures

# Eine Consent-Wall mit HTTP 200 — der Statuscode verraet nichts.
CONSENT_WALL = b"""<html><body>
<div id="onetrust-consent-sdk">Wir verwenden Cookies. Bitte zustimmen.</div>
</body></html>"""

# Eine echte, harmlose Produktseite (gekuerzt), deutlich ueber dem Text-Floor.
PRODUCT_PAGE = (
    b"<html><body><div class='product_main'><h1>A Light in the Attic</h1>"
    b"<p class='price_color'>\xc2\xa351.77</p>"
    b"<p class='availability'>In stock (22 available)</p>"
    b"<p>Es war einmal ein Buch mit einem sehr langen Beschreibungstext, der weit "
    b"ueber zweihundert Bytes hinausgeht, damit der Text-Floor nicht anschlaegt und "
    b"die Seite als legitim erkannt wird.</p></div></body></html>"
)


def test_consent_wall_mit_status_200_wird_erkannt():
    reason = detect(CONSENT_WALL, headers={})
    assert reason == "onetrust_cmp"


def test_echte_produktseite_wird_nicht_geflaggt():
    assert detect(PRODUCT_PAGE, headers={}) is None


def test_cloudflare_just_a_moment():
    body = b"<html><body>Just a moment...</body></html>"
    assert detect(body, headers={}) is not None


def test_cf_mitigated_header():
    assert detect(b"<html><body>egal</body></html>", headers={"cf-mitigated": "challenge"}) == (
        "cf_mitigated_header"
    )


def test_reference_id_regex():
    body = b"<html><body>Access denied. Reference #18.abc</body></html>"
    assert detect(body, headers={}) == "reference_id"


def test_meta_refresh_auf_consent():
    body = (
        b'<html><head><meta http-equiv="refresh" content="0; url=/consent">'
        b"</head><body>x</body></html>"
    )
    assert detect(body, headers={}) == "meta_refresh_consent"


def test_leere_seite_unter_dem_text_floor():
    body = b"<html><body><p>zu kurz</p></body></html>"
    assert detect(body, headers={}) == "empty_below_text_floor"


def test_kurze_legitime_seite_ueber_dem_floor_ist_ok():
    body = (
        b"<html><body><p>" + b"Eine kurze, aber legitime Seite mit genug sichtbarem Text ueber der "
        b"Zweihundert-Byte-Grenze, damit der Floor nicht faelschlich anschlaegt. "
        * 2
        + b"</p></body></html>"
    )
    assert detect(body, headers={}) is None


def test_loader_lehnt_doppelte_namen_ab(tmp_path):
    bad = tmp_path / "sig.yaml"
    bad.write_text(
        textwrap.dedent("""
        version: 1
        signatures:
          - {name: dup, kind: substring, value: a}
          - {name: dup, kind: substring, value: b}
        """),
        encoding="utf-8",
    )
    load_signatures.cache_clear()
    with pytest.raises(ValueError, match="Doppelter Signatur-Name"):
        load_signatures(str(bad))
    load_signatures.cache_clear()


def test_jede_signatur_hat_einen_namen():
    for sig in load_signatures():
        assert isinstance(sig, Signature)
        assert sig.name
