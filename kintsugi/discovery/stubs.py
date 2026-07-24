"""Registrierte, aber noch nicht implementierte Strategien.

F1 ist der Grund, dass diese Stubs existieren statt zu fehlen: books.toscrape.com
hat keine ``sitemap.xml`` (HTTP 404), also weicht das Pack auf ``pagination`` aus
— aber ``sitemap`` muss ein gueltiges Literal bleiben, das zur Laufzeit **laut**
mit Phasennennung scheitert, statt in einen opaken ``KeyError`` zu kippen.

``pagination`` ist hier nur ein Platzhalter, damit die Registry in Phase 0
bereits alle vier Literale traegt; ``kintsugi/discovery/pagination.py`` (I0.9.5)
ersetzt ihn durch die echte Strategie, sobald das Paket es zuletzt importiert.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from kintsugi.discovery.base import DiscoveryContext, register

if TYPE_CHECKING:
    from kintsugi.packs.model import SitePack

__all__ = ["ApiDiscovery", "PaginationStub", "SitemapDiscovery"]


@register("sitemap")
class SitemapDiscovery:
    """sitemap.xml-Auswertung — landet in Phase 1 mit Quelle zwei und drei."""

    def discover(self, pack: SitePack, ctx: DiscoveryContext) -> Iterator[str]:
        raise NotImplementedError(
            "discovery.strategy 'sitemap' ist erst ab Phase 1 (Quelle zwei/drei) "
            "implementiert (docs/08). books.toscrape.com nutzt 'pagination' (F1)."
        )


@register("api")
class ApiDiscovery:
    """API-Discovery — landet in Phase 5 (Stufe-3-Ziele mit API-Gegenprobe)."""

    def discover(self, pack: SitePack, ctx: DiscoveryContext) -> Iterator[str]:
        raise NotImplementedError(
            "discovery.strategy 'api' ist erst ab Phase 5 implementiert "
            "(docs/08, 'Stufe-3-Ziele mit API-Gegenprobe')."
        )


@register("pagination")
class PaginationStub:
    """Phase-0-Platzhalter; von I0.9.5 (pagination.py) ersetzt."""

    def discover(self, pack: SitePack, ctx: DiscoveryContext) -> Iterator[str]:
        raise NotImplementedError(
            "discovery.strategy 'pagination' wird von kintsugi/discovery/"
            "pagination.py (I0.9.5, Phase 0) bereitgestellt."
        )
