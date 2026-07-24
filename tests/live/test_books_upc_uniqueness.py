"""Belegt live, dass die UPC ueber alle 1000 Buecher eindeutig ist (I0.6.10).

**Nur manuell ausfuehren.** Bei 0.5 rps ueber 1000 Detailseiten dauert der Lauf
rund 35 Minuten. Deshalb ``@pytest.mark.live`` und im Standardlauf abgewaehlt;
ausdruecklich starten mit ``uv run pytest -m live tests/live/``.

Der natural_key ``[upc]`` traegt die gesamte SCD-Typ-2-Historisierung (docs/02
§Feldsemantik, docs/09 ADR-007). „Ein falscher Schluessel korrumpiert den
gesamten Bestand rueckwirkend." Dieser Test prueft die Annahme gegen die
Realitaet, statt sie zu glauben.

Rate Limit und Concurrency kommen aus dem geladenen Pack, nicht aus Literalen —
die Politeness-Zusage der README gilt auch fuer Tests.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import Path

import httpx
import pytest
from kintsugi.packs.loader import load_pack
from selectolax.lexbor import LexborHTMLParser

pytestmark = pytest.mark.live

BASE = "https://books.toscrape.com"


def test_upc_ist_ueber_den_ganzen_katalog_eindeutig():
    pack = load_pack("books.toscrape.com", "book", root=Path("packs"))
    rps = pack.fetch.rate_limit_rps
    delay = 1.0 / rps if rps > 0 else 0.0
    upc_selector = pack.extract.sources[0].fields["upc"].selector  # type: ignore[union-attr]

    with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as client:
        product_urls: list[str] = []
        for n in range(pack.discovery.page_start, (pack.discovery.page_stop or 50) + 1):
            resp = client.get(f"/catalogue/page-{n}.html")
            resp.raise_for_status()
            for href in re.findall(r'<h3><a href="([^"]+)"', resp.text):
                product_urls.append("/catalogue/" + href.replace("../../../", ""))
            time.sleep(delay)

        upcs: dict[str, str] = {}
        collisions: list[tuple[str, str, str]] = []
        for url in product_urls:
            resp = client.get(url)
            resp.raise_for_status()
            node = LexborHTMLParser(resp.text).css_first(upc_selector)
            upc = node.text() if node else ""
            if upc in upcs:
                collisions.append((upc, upcs[upc], url))
            upcs[upc] = url
            time.sleep(delay)

    counted = Counter(list(upcs) + [c[0] for c in collisions])
    print(f"walked {len(product_urls)} products, {len(upcs)} distinct UPCs")
    if collisions:
        for upc, first, second in collisions:
            print(f"KOLLISION upc={upc}: {first} <-> {second}")
    assert not collisions, f"UPC-Kollisionen: {[c[0] for c in collisions]}"
    assert len(product_urls) == 1000
    assert len(upcs) == 1000
    assert max(counted.values()) == 1
