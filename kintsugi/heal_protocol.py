"""Das Heiler-Protokoll und der NullHealer (I1.4.3).

docs/01-architecture.md §Erweiterbarkeit (Strategy-Tabelle) und docs/08-roadmap.md
§Phase 1 DoD: „Ohne Heilung ist das erwartete Ergebnis ueberall ``escalated``
beziehungsweise ``no_action``." Phase 1 baut den Klassifikator, **nicht** den
Heiler. Damit ``classify`` trotzdem gegen einen echten Vertrag laeuft, gibt es
hier das ``Healer``-Protokoll, das Faehigkeiten-Flag und einen ``NullHealer``,
dessen ``capabilities()`` ``NONE`` meldet und dessen ``propose()`` wirft.

Der Klassifikator liest nur das Faehigkeiten-Flag (``HealerCapabilities``); er
ruft nie ``propose()``. Dadurch ist ``classify`` unter ``NONE`` strukturell
unfaehig, ``auto_healed`` zu liefern — die halbe DoD ist eine Typbedingung, keine
Gewohnheit.
"""

from __future__ import annotations

from enum import Flag, auto
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from kintsugi.packs.model import SitePack

__all__ = ["Healer", "HealerCapabilities", "NullHealer"]


class HealerCapabilities(Flag):
    """Was ein Heiler kann. Die drei Reparaturstufen aus docs/04 als Flags.

    Phase 1 ist ``NONE`` (kein Bit gesetzt). Die Stufen kommen in Phase 2:
    ``VALUE_ANCHOR`` (Wertanker), ``DOM_DIFF`` (DOM-Diff), ``LLM`` (LLM-Vorschlag).
    """

    NONE = 0
    VALUE_ANCHOR = auto()
    DOM_DIFF = auto()
    LLM = auto()


@runtime_checkable
class Healer(Protocol):
    """Der Vertrag, den ein Heiler erfuellt. In Phase 2 gefuellt."""

    def capabilities(self) -> HealerCapabilities:
        """Welche Reparaturstufen dieser Heiler beherrscht."""
        ...

    def propose(self, pack: SitePack) -> object:
        """Erzeugt einen Site-Pack-Patch-Vorschlag (Phase 2)."""
        ...


class NullHealer:
    """Der Phase-1-Heiler: kann nichts, schlaegt nichts vor.

    ``capabilities()`` ist ``NONE``; ``propose()`` wirft, weil es in Phase 1
    keinen Reparaturpfad gibt. Ein ``classify``-Aufruf mit diesen Faehigkeiten
    kann nur ``escalated`` oder ``no_action`` liefern, nie ``auto_healed``.
    """

    def capabilities(self) -> HealerCapabilities:
        return HealerCapabilities.NONE

    def propose(self, pack: SitePack) -> object:
        raise NotImplementedError("Phase 1 hat keinen Heiler (NullHealer.propose)")
