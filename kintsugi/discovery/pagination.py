"""pagination-Discovery mit 404-Terminierung (I0.9.5).

docs/02-site-packs.md §Beispiel (``discovery``-Block) und docs/01-architecture.md
§Komponenten/Discovery ("Getrennt vom Fetch, weil sich Paginierungsschemata
unabhaengig vom Seitenlayout aendern").

F1 ist der Grund, dass es dieses Modul gibt: books.toscrape.com liefert **keine**
``sitemap.xml`` und **keine** ``robots.txt`` (beide HTTP 404), das Pack laeuft
deshalb ueber ``catalogue/page-{n}.html``. Verifiziert: 20 Produktlinks je Seite,
``page-51.html`` -> 404, 1000 Produkte gesamt — bequem ueber der DoD-Schwelle.

Jede Index-Seite laeuft durch **denselben** Fetcher wie jede andere Anfrage, die
robots-Pruefung und der 0.5-rps-Limiter gelten also auch hier. Die Ausgabe ist
stabil sortiert — die Duplikatregel des Record-Writers (I0.9.3) haengt an dieser
Reihenfolge.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from kintsugi.discovery.base import DiscoveryContext, register
from kintsugi.fetch.robots import RobotsDenied

if TYPE_CHECKING:
    from kintsugi.packs.model import SitePack

__all__ = ["PaginationDiscovery"]

_DEFAULT_LINK_SELECTOR = "article.product_pod h3 a"
# Harte Sicherheitsschranke, falls ein Pager nie 404t und nie leer wird.
_HARD_MAX_PAGES = 5000


@register("pagination")
class PaginationDiscovery:
    """Laeuft ``url_template`` mit ``{n}`` ab, bis 404 oder eine leere Seite."""

    def discover(self, pack: SitePack, ctx: DiscoveryContext) -> Iterator[str]:
        disc = pack.discovery
        template = disc.url_template
        if not template:
            raise ValueError("pagination braucht discovery.url_template")
        pattern = re.compile(disc.url_pattern) if disc.url_pattern else None
        selector = disc.link_selector or _DEFAULT_LINK_SELECTOR
        max_pages = disc.page_stop or _HARD_MAX_PAGES

        seen: set[str] = set()
        yielded = 0
        n = disc.page_start
        pages_walked = 0

        while pages_walked < max_pages:
            index_url = template.replace("{n}", str(n))
            try:
                result = ctx.fetcher.fetch(index_url)
            except RobotsDenied:
                # robots verbietet die Index-Seite -> nichts zu entdecken.
                ctx.counters.skip_robots()
                return
            ctx.counters.record_http(result.http_status, fetch_ms=float(result.elapsed_ms))

            # 404 oder irgendein Nicht-2xx beendet den Lauf (Terminator).
            if not (200 <= result.http_status < 300):
                return

            new_on_page = 0
            tree = LexborHTMLParser(result.text)
            for node in tree.css(selector):
                href = node.attributes.get("href")
                if not href:
                    continue
                # Relative hrefs erst aufloesen, dann gegen url_pattern filtern.
                absolute = urljoin(index_url, href)
                if pattern is not None and not pattern.search(absolute):
                    continue
                if absolute in seen:
                    continue
                seen.add(absolute)
                if yielded >= disc.max_urls_per_run:
                    return
                yield absolute
                yielded += 1
                new_on_page += 1
                ctx.counters.urls_discovered += 1

            # Seite ohne neue URLs: ein Pager, der auf die letzte Seite klemmt,
            # statt zu 404en — sonst liefe der Walk endlos.
            if new_on_page == 0:
                return
            n += 1
            pages_walked += 1
