"""JSON-LD-Extraktor (docs/01 §Extraction Stufe 2, I1.5.2b).

docs/01 reiht JSON-LD als zweitliebsten Pfad ein, ueber embedded JSON — „der
wichtigste Hebel fuer Wartungsarmut", weil schema.org-Markup sich selten aendert.
Der Plan registrierte ``jsonld`` als ``NotImplementedError``-Stub und liess die
zweite Sprosse der Kette damit tot; hier wird sie gebaut und die Prioritaet
end to end pruefbar.

Sammelt jeden ``<script type="application/ld+json">``, ``json.loads`` je Block
(toleriert ein Top-Level-Array und einen ``@graph``-Wrapper), waehlt das erste
Objekt in **Dokumentreihenfolge**, dessen ``@type`` (case-sensitiv) zum Pack
passt, und bildet die Felder ueber jsonpath-ng relativ zu diesem Objekt ab. Ein
kaputter Block wird uebersprungen, nicht fatal — eine Seite mit einem defekten
und einem gueltigen Block extrahiert trotzdem. Null Treffer sind ein typisierter
Fehltreffer: die Kette faellt auf ``embedded_json`` / ``css`` durch.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from selectolax.lexbor import LexborHTMLParser

from kintsugi.extract.base import register
from kintsugi.extract.jsonpath_fields import map_fields
from kintsugi.packs.model import JsonLdSource

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["JsonLdExtractor"]


def _blocks(doc: LexborHTMLParser) -> Iterator[object]:
    """Jeder ld+json-Block, geparst; kaputte Bloecke werden uebersprungen."""
    for node in doc.css('script[type="application/ld+json"]'):
        text = node.text()
        if not text:
            continue
        try:
            yield json.loads(text)
        except json.JSONDecodeError:
            continue  # ein kaputter Block ist nicht fatal


def _candidates(node: object) -> Iterator[dict[str, object]]:
    """Objekte in Dokumentreihenfolge; entpackt Arrays und ``@graph``-Wrapper."""
    if isinstance(node, list):
        for item in node:
            yield from _candidates(item)
    elif isinstance(node, dict):
        graph = node.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _candidates(item)
        else:
            yield cast("dict[str, object]", node)


def _find(doc: LexborHTMLParser, type_: str) -> dict[str, object] | None:
    for block in _blocks(doc):
        for candidate in _candidates(block):
            if candidate.get("@type") == type_:  # case-sensitiv, erster Treffer gewinnt
                return candidate
    return None


class JsonLdExtractor:
    """Zieht Felder aus ``application/ld+json`` (schema.org @type)."""

    def extract(self, doc: object, source: object) -> dict[str, object]:
        assert isinstance(doc, LexborHTMLParser)
        assert isinstance(source, JsonLdSource)
        obj = _find(doc, source.type)
        if obj is None:
            return {}  # typisierter Fehltreffer -> Kette faellt durch
        return map_fields(obj, source.fields) if source.fields else dict(obj)

    def extract_all(self, doc: object, source: object) -> list[dict[str, object]]:
        """JSON-LD ist von Natur aus einzeilig: der erste ``@type``-Treffer.

        schema.org-Markup beschreibt in dieser Phase genau eine Entitaet je Seite
        (ein Produkt, ein Rezept); mehr als einen Treffer zu einer Liste zu
        entfalten (``ItemList``) ist bewusst nicht Teil von Phase 1. Daher liefert
        ``extract_all`` hoechstens eine Zeile — die Kette bleibt uniform, ohne dass
        jsonld ein Mehrzeilen-Verhalten vortaeuscht, das die realen Packs (#104
        embedded_json, #105 xhr) gar nicht von jsonld verlangen.
        """
        assert isinstance(doc, LexborHTMLParser)
        assert isinstance(source, JsonLdSource)
        obj = _find(doc, source.type)
        if obj is None:
            return []
        return [map_fields(obj, source.fields) if source.fields else dict(obj)]


register("jsonld", JsonLdExtractor())
