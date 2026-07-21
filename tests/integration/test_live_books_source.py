"""Haelt die Live-Befunde zu books.toscrape.com als ausfuehrbaren Test fest.

Diese Tatsachen widerlegen das Site-Pack-Beispiel in `docs/02-site-packs.md`,
das `discovery.strategy: sitemap` mit `sitemap_url` deklariert. Beides liefert
404. Solange das nur in einer Commit-Nachricht steht, faellt niemandem auf,
wenn die Quelle es spaeter aendert.

Alle Tests hier tragen `live` und sind damit aus dem Standardlauf abgewaehlt.
Sie laufen nur unter ``uv run pytest -m live``.
"""

from __future__ import annotations

import httpx
import pytest

BASE = "https://books.toscrape.com"
TIMEOUT = httpx.Timeout(connect=10, read=20, write=10, pool=5)

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def client():
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as c:
        yield c


@pytest.mark.parametrize("path", ["/robots.txt", "/sitemap.xml"])
def test_weder_robots_noch_sitemap_existieren(client, path):
    """F1: beide liefern 404 — deshalb ist `strategy: sitemap` nicht umsetzbar."""
    assert client.get(f"{BASE}{path}").status_code == 404


def test_paginierung_hat_genau_fuenfzig_seiten(client):
    """F1: die tragfaehige Discovery-Strategie ist Paginierung, nicht Sitemap."""
    assert client.get(f"{BASE}/catalogue/page-50.html").status_code == 200
    assert client.get(f"{BASE}/catalogue/page-51.html").status_code == 404


def test_erste_seite_listet_zwanzig_produkte(client):
    """1000 Produkte gesamt — die Phase-0-DoD verlangt mindestens 200 Records."""
    from selectolax.lexbor import LexborHTMLParser

    tree = LexborHTMLParser(client.get(f"{BASE}/catalogue/page-1.html").text)
    assert len(tree.css("h3 > a")) == 20


def test_produktseite_traegt_die_im_pack_erwarteten_felder(client):
    """F2: die vier Selektoren aus docs/02 treffen die echte Seite."""
    from selectolax.lexbor import LexborHTMLParser

    url = f"{BASE}/catalogue/a-light-in-the-attic_1000/index.html"
    tree = LexborHTMLParser(client.get(url).text)

    assert tree.css_first("div.product_main > h1").text() == "A Light in the Attic"
    assert tree.css_first("p.price_color").text() == "£51.77"
    assert "In stock" in tree.css_first("p.availability").text()

    upc = tree.css_first("table.table-striped tr:nth-child(1) td").text()
    assert upc == "a897fe39b1053632"
    assert len(upc) == 16
    assert all(c in "0123456789abcdef" for c in upc)
