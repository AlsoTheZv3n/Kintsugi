"""Prueft den blob_key-Builder (docs/03 §Bronze, ADR-009 Kontrakt 3/4).

Zwei Fallen sind hier real: Rueckwaertsschraegstriche auf dem Windows-Host, die
den Schluessel beim Umzug auf SeaweedFS brechen wuerden, und ein naiver
Zeitstempel, der einen Lauf kurz vor Mitternacht je nach lokaler Zone in den
falschen Monat legt.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from kintsugi.storage.blobkey import build_blob_key, domain_of

HASH = hashlib.sha256(b"beispielinhalt").digest()
HEX = HASH.hex()
KEY_RE = re.compile(r"^raw/[a-z0-9.\-]+/\d{4}/\d{2}/[a-f0-9]{64}\.gz$")


def test_format_und_keine_rueckwaertsschraegstriche():
    key = build_blob_key(
        "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
        HASH,
        datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
    )
    assert "\\" not in key, "Rueckwaertsschraegstrich — bricht auf SeaweedFS"
    assert KEY_RE.match(key), key
    assert key == f"raw/books.toscrape.com/2026/07/{HEX}.gz"


def test_domain_segment_ist_der_reine_host():
    key = build_blob_key(
        "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
        HASH,
        datetime(2026, 7, 21, tzinfo=UTC),
    )
    assert key.split("/")[1] == "books.toscrape.com"


@pytest.mark.parametrize(
    ("url", "erwartet"),
    [
        ("https://Books.ToScrape.com/x", "books.toscrape.com"),  # kleingeschrieben
        ("https://user:pw@books.toscrape.com:8443/x", "books.toscrape.com"),  # ohne Userinfo/Port
        ("http://EXAMPLE.ORG/", "example.org"),
    ],
)
def test_domain_of_normalisiert(url, erwartet):
    assert domain_of(url) == erwartet


def test_url_ohne_host_wirft():
    with pytest.raises(ValueError, match="ohne Host"):
        build_blob_key("nicht-mal-eine-url", HASH, datetime(2026, 7, 21, tzinfo=UTC))


def test_monat_wird_nach_utc_gebucketet():
    """Kurz vor Monatsende in einer Ostzone gehoert der Abruf in den UTC-Monat."""
    # 1. Feb 07:30 in UTC+9 ist 31. Jan 22:30 UTC -> Monat 01, nicht 02.
    ostzone = timezone(timedelta(hours=9))
    lokal = datetime(2026, 2, 1, 7, 30, tzinfo=ostzone)
    key = build_blob_key("https://x.test/a", HASH, lokal)
    assert key.split("/")[2:4] == ["2026", "01"], key


def test_naiver_zeitstempel_wird_abgelehnt():
    with pytest.raises(ValueError, match="zeitzonenlos"):
        build_blob_key("https://x.test/a", HASH, datetime(2026, 7, 21, 12, 0))


def test_falsche_hashlaenge_wird_abgelehnt():
    with pytest.raises(ValueError, match="32"):
        build_blob_key("https://x.test/a", b"zu kurz", datetime(2026, 7, 21, tzinfo=UTC))


def test_gleicher_inhalt_ueber_monatsgrenze_ergibt_verschiedene_erststrings():
    """Belegt, warum der Monat NICHT Teil der Identitaet sein darf.

    Derselbe content_hash im Januar und Februar erzeugt zwei verschiedene
    Schluessel. Deshalb muss der Writer ueber content_hash nachschlagen und den
    ersten Schluessel wiederverwenden, statt den Monat als Identitaet zu nehmen.
    """
    jan = build_blob_key("https://x.test/a", HASH, datetime(2026, 1, 15, tzinfo=UTC))
    feb = build_blob_key("https://x.test/a", HASH, datetime(2026, 2, 15, tzinfo=UTC))
    assert jan != feb
    assert jan.rsplit("/", 1)[-1] == feb.rsplit("/", 1)[-1]  # gleicher sha256-Dateiname
