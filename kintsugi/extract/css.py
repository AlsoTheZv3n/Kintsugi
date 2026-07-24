"""CssExtractor auf selectolax (lexbor), None bei Fehltreffer.

Die tragende Regel der ganzen Erkennungspraemisse (docs/01 §Validation, docs/02
§Feldsemantik: min_fill_rate ist „der eigentliche Wachhund"): ein Selektor, der
nichts trifft, liefert ``None`` fuer das Feld und wirft nie. Ein kaputter
Selektor muss als Fill-Rate-Einbruch auffallen, nicht als Traceback, der den
Lauf killt. Ein syntaktisch ungueltiger Selektor ist dagegen ein Pack-Fehler,
der zur Ladezeit auffaellt (statische Pruefung I0.6.8), nicht hier.

Rohtext wird zurueckgegeben; Trimmen und Waehrungsparsing gehoeren in die
Transform-Kette, nicht hierher. Die leichte Text-Normalisierung ``strip=True``
entfernt nur den Knoten-Whitespace, damit der Rohwert dem entspricht, was die
Seite sichtbar zeigt.
"""

from __future__ import annotations

from typing import cast

from selectolax.lexbor import LexborHTMLParser, LexborNode

from kintsugi.extract.base import register
from kintsugi.packs.model import CssSource, FieldExtract


def _field_value(node: LexborHTMLParser | LexborNode, field: FieldExtract) -> str | None:
    # selectolax typisiert css_first als nicht-optional, liefert zur Laufzeit
    # aber None bei Fehltreffer — die wahre Signatur ist LexborNode | None.
    found = cast("LexborNode | None", node.css_first(field.selector))
    if found is None:
        return None
    if field.attr is not None:
        return found.attributes.get(field.attr)
    return found.text(strip=True) or None


def _row(node: LexborHTMLParser | LexborNode, fields: dict[str, FieldExtract]) -> dict[str, object]:
    # dict[str, object] statt str|None: das Extractor-Protokoll ist auf strukturierte
    # Quellen gelockert; css liefert weiterhin nur str|None-Werte hinein.
    row: dict[str, object] = {name: _field_value(node, field) for name, field in fields.items()}
    return row


class CssExtractor:
    """Zieht Felder per CSS-Selektor. row_selector null = eine Entitaet pro Seite."""

    def extract(self, doc: object, source: object) -> dict[str, object]:
        assert isinstance(source, CssSource)
        parser = doc
        assert isinstance(parser, LexborHTMLParser)
        if source.row_selector is not None:
            node = cast("LexborNode | None", parser.css_first(source.row_selector))
            if node is None:
                empty: dict[str, object] = dict.fromkeys(source.fields)
                return empty
            return _row(node, source.fields)
        return _row(parser, source.fields)

    def extract_all(self, doc: LexborHTMLParser, source: CssSource) -> list[dict[str, object]]:
        """Alle Zeilen: bei row_selector eine je Treffer, sonst genau eine."""
        if source.row_selector is not None:
            return [_row(node, source.fields) for node in doc.css(source.row_selector)]
        return [_row(doc, source.fields)]


register("css", CssExtractor())
