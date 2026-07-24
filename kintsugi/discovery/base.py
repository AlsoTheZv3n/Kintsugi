"""DiscoveryStrategy-Protokoll, Kontext und Strategie-Registry.

docs/01-architecture.md §Komponenten/Discovery und §Erweiterbarkeit
(Erweiterungspunkt ``DiscoveryStrategy``); docs/02-site-packs.md §Beispiel
(``discovery.strategy: sitemap | pagination | seed_list | api``).

Die Registry-Schluessel und das Pack-Literal ``DiscoveryStrategyName`` lesen aus
derselben Quelle — ein Test in ``tests/unit/test_discovery_registry.py`` haelt
sie deckungsgleich, damit Schema-Validierung und Strategie-Lookup nie
auseinanderdriften (F1: ``sitemap`` bleibt ein gueltiges Literal, das zur
Laufzeit laut mit Phasennennung scheitert, statt in einen KeyError zu kippen).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

if TYPE_CHECKING:
    from kintsugi.fetch.base import Fetcher
    from kintsugi.packs.model import SitePack
    from kintsugi.quality.counters import RunCounters

__all__ = [
    "REGISTRY",
    "DiscoveryContext",
    "DiscoveryStrategy",
    "get_strategy",
    "register",
]


@dataclass(frozen=True, slots=True)
class DiscoveryContext:
    """Der Zustand, den eine Strategie zum Laufen braucht.

    Der ``fetcher`` traegt robots-Pruefung und Rate-Limiter bereits in sich —
    eine Strategie ruft nie an ihm vorbei, sonst faellt die Politeness-Zusage
    aus README §Compliance fuer genau den Verkehr, der die Index-Seiten trifft.
    """

    fetcher: Fetcher
    run_id: UUID
    counters: RunCounters


@runtime_checkable
class DiscoveryStrategy(Protocol):
    """Liefert die zu holenden URLs in stabiler Reihenfolge."""

    def discover(self, pack: SitePack, ctx: DiscoveryContext) -> Iterator[str]: ...


REGISTRY: dict[str, type[DiscoveryStrategy]] = {}


def register(name: str) -> Callable[[type[DiscoveryStrategy]], type[DiscoveryStrategy]]:
    """Traegt eine Strategie-Klasse unter ``name`` ein.

    Eine spaetere Registrierung fuer denselben Namen ersetzt die fruehere. Das
    ist beabsichtigt: ``pagination`` startet in Phase 0 als Stub (base) und wird
    von ``kintsugi/discovery/pagination.py`` (I0.9.5) zur echten Strategie
    aufgewertet, sobald das Paket dieses Modul zuletzt importiert.
    """

    def decorate(cls: type[DiscoveryStrategy]) -> type[DiscoveryStrategy]:
        REGISTRY[name] = cls
        return cls

    return decorate


def get_strategy(name: str) -> DiscoveryStrategy:
    """Instanziiert die unter ``name`` registrierte Strategie.

    Ein unbekannter Name ist ein Programmierfehler (die Pack-Validierung
    beschraenkt das Literal schon vorher) und fliegt als ``KeyError``.
    """
    return REGISTRY[name]()
