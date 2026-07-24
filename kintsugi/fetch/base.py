"""Die Naht, in die jeder Fetcher steckt (docs/01 §Fetch, Strategy-Muster).

``HttpFetcher`` (Phase 0) und ``BrowserFetcher`` (Phase 5) sind hinter einem
Protokoll austauschbar, weil der Fetcher eine Eigenschaft des Site-Packs ist und
durch Heilung gewechselt werden kann, wenn eine Quelle auf Client-Rendering
umstellt. ``FetchResult`` ist ohne Netz konstruierbar — der Mutations-Harness in
Phase 1 mutiert einen gespeicherten Body und reicht ihn durch genau diesen Typ.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class FetchOutcome(StrEnum):
    """Ausgang eines Abrufs. rate_limited und blocked sind bewusst getrennt:
    docs/07 N01/N04 verlangen, dass die Pipeline ein 429 von einer Consent-Wall
    und beide von einem Datenbruch unterscheidet."""

    ok = "ok"
    not_modified = "not_modified"
    not_found = "not_found"
    rate_limited = "rate_limited"
    blocked = "blocked"
    error = "error"


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Rohes Ergebnis eines Abrufs. ``body`` ist immer bytes, nie str — der
    Snapshot-Hash aus docs/03 §Bronze ist sha256 des Rohkoerpers."""

    url: str
    final_url: str
    http_status: int
    headers: Mapping[str, str]
    body: bytes
    content_type: str | None
    encoding: str
    elapsed_ms: int
    fetcher: str
    from_cache: bool
    outcome: FetchOutcome

    @property
    def text(self) -> str:
        """Dekodiert ``body`` mit dem aufgeloesten ``encoding`` — der einzige Ort,
        an dem aus dem Rohkoerper Text wird."""
        return self.body.decode(self.encoding, errors="replace")


@runtime_checkable
class Fetcher(Protocol):
    """Strategy-Protokoll. Drittanbieter brauchen keinen Import einer Basisklasse."""

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult: ...
