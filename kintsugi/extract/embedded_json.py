"""Extraktor fuer eingebettetes JSON (docs/01 §Extraction Stufe 3, I1.5.2).

Zwei Modi:

1. **``script_id``** — ``<script id="__NEXT_DATA__">`` / ``__NUXT__`` finden,
   ``json.loads``, die Nutzlast ueber ``root`` adressieren.
2. **``inline_js_var``** — **F5.** ``quotes.toscrape.com/js`` hat null
   ``.quote``-Elemente; die Daten liegen als ``var data = [ {...} ]`` auf einem
   script-Tag **ohne id**, was der script_id-Modus nicht adressieren kann. Der
   Modus findet die Zuweisung an ``var_name``, schneidet das Objekt-/Array-Literal
   per balanciertem Klammer-Scan (string-bewusst, damit Klammern *in* Strings
   nicht mitzaehlen) und parst es. Ohne diesen Modus ist Quelle zwei in Phase 1
   gar nicht extrahierbar (der Playwright-Fetcher kommt erst in Phase 5).

Ein abgeschnittenes Literal wirft ``EmbeddedJsonError`` (typisiert), nie einen
blanken ``json.JSONDecodeError`` — der Aufrufer soll den Bruch als Extraktions-
fehler behandeln, nicht als generischen Parserfehler.

Rueckgabe sind die rohen Zeilen-Objekte (Schluessel = Feldnamen); die
Transform-/derived_from-Kette liegt wie bei css spaeter. ``extract`` liefert die
erste Entitaet (Prioritaetskette), ``extract_all`` alle Zeilen (Mehrzeilen-Quellen
wie quotes).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, cast

from jsonpath_ng import parse as jsonpath_parse
from selectolax.lexbor import LexborHTMLParser, LexborNode

from kintsugi.extract.base import register
from kintsugi.packs.model import EmbeddedJsonSource

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["EmbeddedJsonError", "EmbeddedJsonExtractor"]

_OPEN_TO_CLOSE = {"{": "}", "[": "]"}
_QUOTES = frozenset({"'", '"', "`"})


class EmbeddedJsonError(Exception):
    """Das eingebettete JSON ist unbrauchbar (abgeschnitten, unparsbar)."""


def _slice_literal(text: str, start: int) -> str:
    """Schneidet ab ``start`` (einem ``{``/``[``) das balancierte Literal.

    String-bewusst: Klammern innerhalb von '..', ".." oder `..` zaehlen nicht,
    Escapes werden respektiert. Laeuft der Scan ueber das Ende (unbalanciert =
    abgeschnitten), wirft er ``EmbeddedJsonError`` statt still zu viel zu greifen.
    """
    open_ch = text[start]
    close_ch = _OPEN_TO_CLOSE[open_ch]
    depth = 0
    in_str: str | None = None
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in _QUOTES:
            in_str = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise EmbeddedJsonError(f"unbalanciertes Literal ab Index {start} (abgeschnitten?)")


def _script_texts(doc: LexborHTMLParser) -> Iterator[str]:
    for node in doc.css("script"):
        text = node.text()
        if text:
            yield text


def _find_var_literal(doc: LexborHTMLParser, var_name: str) -> str:
    """Findet die Zuweisung ``var/let/const <name> =`` oder ``window.<name> =``.

    Verankert am Deklarations-Schluesselwort plus exaktem Namen plus ``=`` direkt
    vor ``[``/``{`` — der blosse Name in einem String-Literal (Decoy) matcht damit
    nicht.
    """
    pattern = re.compile(
        r"(?:(?:var|let|const)\s+|(?:window|globalThis|self)\s*\.\s*)"
        + re.escape(var_name)
        + r"\s*=\s*(?=[\[{])"
    )
    for text in _script_texts(doc):
        match = pattern.search(text)
        if match is not None:
            return _slice_literal(text, match.end())
    raise EmbeddedJsonError(f"keine Zuweisung an {var_name!r} in einem <script> gefunden")


def _loads(text: str, ctx: str) -> object:
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EmbeddedJsonError(f"{ctx}: kein gueltiges JSON: {exc}") from exc
    return parsed


def _load(doc: LexborHTMLParser, source: EmbeddedJsonSource) -> object | None:
    if source.var_name is not None:
        return _loads(_find_var_literal(doc, source.var_name), f"var {source.var_name!r}")
    # script_id-Modus. css_first ist selectolax-seitig als nicht-optional typisiert,
    # liefert zur Laufzeit aber None bei Fehltreffer -> casten wie in css.py.
    node = cast("LexborNode | None", doc.css_first(f'script[id="{source.script_id}"]'))
    if node is None:
        return None  # typisierter Fehltreffer -> Kette faellt durch
    return _loads(node.text() or "", f"script#{source.script_id}")


def _navigate(payload: object, root: str | None) -> object:
    if not root:
        return payload
    expr = jsonpath_parse(root if root.startswith("$") else "$." + root)
    found: list[object] = [match.value for match in expr.find(payload)]
    if not found:
        return None
    return found[0] if len(found) == 1 else found


def _rows(doc: LexborHTMLParser, source: EmbeddedJsonSource) -> list[dict[str, object]]:
    payload = _load(doc, source)
    if payload is None:
        return []
    target = _navigate(payload, source.root)
    if isinstance(target, list):
        return [row for row in target if isinstance(row, dict)]
    if isinstance(target, dict):
        return [target]
    return []


class EmbeddedJsonExtractor:
    """Zieht Felder aus eingebettetem JSON (script_id oder inline_js_var)."""

    def extract(self, doc: object, source: object) -> dict[str, object]:
        assert isinstance(doc, LexborHTMLParser)
        assert isinstance(source, EmbeddedJsonSource)
        rows = _rows(doc, source)
        return dict(rows[0]) if rows else {}

    def extract_all(self, doc: object, source: object) -> list[dict[str, object]]:
        assert isinstance(doc, LexborHTMLParser)
        assert isinstance(source, EmbeddedJsonSource)
        return [dict(row) for row in _rows(doc, source)]


register("embedded_json", EmbeddedJsonExtractor())
