"""seed_list-Discovery: die deklarierten Seeds, dedupliziert und gefiltert.

docs/02-site-packs.md §Beispiel (``discovery.strategy: seed_list``). Die
einfachste Strategie: keine HTTP-Abrufe, die URLs stehen bereits im Pack.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from kintsugi.discovery.base import DiscoveryContext, register

if TYPE_CHECKING:
    from kintsugi.packs.model import SitePack

__all__ = ["SeedListDiscovery"]


@register("seed_list")
class SeedListDiscovery:
    """Liefert ``discovery.seeds`` in deklarierter Reihenfolge.

    Exakte Duplikate fallen raus (erstes Vorkommen gewinnt), Seeds ausserhalb
    von ``url_pattern`` werden verworfen, und bei ``max_urls_per_run`` ist
    Schluss. Reihenfolge ist stabil — die Duplikatregel des Record-Writers
    (I0.9.3) haengt daran.
    """

    def discover(self, pack: SitePack, ctx: DiscoveryContext) -> Iterator[str]:
        disc = pack.discovery
        pattern = re.compile(disc.url_pattern) if disc.url_pattern else None
        seen: set[str] = set()
        yielded = 0
        for url in disc.seeds:
            if url in seen:
                continue
            seen.add(url)
            if pattern is not None and not pattern.search(url):
                continue
            if yielded >= disc.max_urls_per_run:
                return
            yield url
            yielded += 1
            ctx.counters.urls_discovered += 1
