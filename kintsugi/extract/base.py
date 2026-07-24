"""Extractor-Protokoll und namensbasierte Registry (docs/01 §Extraction).

Die priorisierte Reihenfolge (API -> JSON-LD -> embedded JSON -> XHR -> CSS ->
LLM) ist „der wichtigste Hebel fuer Wartungsarmut", also ist die Kette, nicht
der CSS-Extraktor, das Hauptartefakt dieses Epics. Kein ``const``-Kind: ADR-013
hat den Mechanismus verworfen; Felder ohne DOM-Quelle entstehen ueber
``derived_from`` (siehe ``derive.py``), und die Kette bleibt feldagnostisch.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# Welche Phase den Executor einer Art liefert. Nur css laeuft in Phase 0.
_STUB_PHASE: dict[str, str] = {
    "jsonld": "Phase 1",
    "embedded_json": "Phase 1",
    "xhr": "Phase 1",
    "llm": "Phase 4",
    "api": "Phase 5",
}


class PackConfigError(Exception):
    """Ein Site-Pack nennt eine unbekannte Extraktions-Art (Konfigurationsfehler)."""


@runtime_checkable
class Extractor(Protocol):
    """Zieht die deklarierten Felder aus einem geparsten Dokument.

    Gibt je Feld einen rohen Textwert oder ``None`` zurueck; ``None`` und ``""``
    zaehlen beide als leer.
    """

    def extract(self, doc: object, source: object) -> dict[str, str | None]: ...


class _StubExtractor:
    """Registrierte, aber noch nicht gebaute Art; nennt die liefernde Phase."""

    def __init__(self, kind: str, phase: str) -> None:
        self._kind = kind
        self._phase = phase

    def extract(self, doc: object, source: object) -> dict[str, str | None]:
        raise NotImplementedError(f"Extraktor fuer kind={self._kind!r} kommt in {self._phase}")


REGISTRY: dict[str, Extractor] = {
    kind: _StubExtractor(kind, phase) for kind, phase in _STUB_PHASE.items()
}


def register(kind: str, extractor: Extractor) -> None:
    """Registriert einen Extraktor unter seiner Art (css in I0.8.2)."""
    REGISTRY[kind] = extractor


def resolve(kind: str) -> Extractor:
    """Der Extraktor zur Art, oder ``PackConfigError`` bei unbekannter Art."""
    extractor = REGISTRY.get(kind)
    if extractor is None:
        raise PackConfigError(f"unbekannte Extraktions-Art im Site-Pack: {kind!r}")
    return extractor
