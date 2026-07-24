"""XHR-Extraktor: die Zeilen einer Seite aus ihrem eigenen AJAX-Endpunkt (I1.5.3).

docs/01 §Extraction Punkt 4 („XHR-Endpunkt der Seite … brechen um Groessenordnungen
seltener als Klassennamen"). Der Endpunkt wird **immer** durch den ``HttpFetcher``
geholt (robots via protego, Rate Limit, Retry, bedingte Anfragen, Snapshot vor
Parsing), nie ueber ein blankes ``httpx`` — sonst gaebe es einen zweiten HTTP-Pfad,
der das robots-Gate und die Zulassungspruefung (I1.5.1) umginge (F4). Deshalb
importiert dieses Modul ``httpx`` bewusst nicht.

Ein Nicht-200-Status oder ein Nicht-JSON-``content_type`` ist ein **typisierter
Fehltreffer** (leere Liste), keine Ausnahme: die Prioritaetskette faellt auf die
naechste Quelle durch, statt den Lauf zu reissen. Ein robots-Deny ist dasselbe —
der Fetcher wirft ``RobotsDenied``, **bevor** er eine Anfrage stellt, also null
Requests und ein Miss.

Anders als css/embedded_json/jsonld parst dieser Extraktor kein bereits geholtes
Dokument: er braucht den Lauf-Fetcher und die entdeckte Seiten-URL (fuer die
Template-Platzhalter). Er wird deshalb mit dem Fetcher konstruiert und **nicht** als
parameterlose Registry-Singleton registriert; die Verdrahtung in die Prioritaetskette
(Fetcher + Seiten-URL durchreichen) kommt mit dem Runner-Mehrzeilen-Pfad. Der
``snapshot``-Rueckruf persistiert die Antwort **vor** dem Parsen (ADR-009, Bronze);
in Produktion umschliesst er ``save_snapshot`` (content_type application/json),
im Test ist er eine Attrappe.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlsplit

from jsonpath_ng import parse as jsonpath_parse

from kintsugi.extract.jsonpath_fields import apply_field_map, compile_field_map
from kintsugi.fetch.robots import RobotsDenied
from kintsugi.packs.model import XhrSource

if TYPE_CHECKING:
    from collections.abc import Callable

    from kintsugi.fetch.base import Fetcher, FetchResult

__all__ = ["XhrExtractor"]


def _substitute(text: str, page_params: dict[str, list[str]]) -> str:
    """Ersetzt ``{name}``-Platzhalter durch den Query-Parameter ``name`` der Seiten-URL.

    Bewusst ``str.replace`` statt ``str.format``: ein literales ``{`` im Endpunkt
    (selten, aber moeglich) darf nicht als Feld interpretiert werden und werfen.
    """
    out = text
    for name, values in page_params.items():
        out = out.replace("{" + name + "}", values[0] if values else "")
    return out


def _build_endpoint(source: XhrSource, page_url: str) -> str:
    """Baut die Endpunkt-URL: Template-Platzhalter fuellen, dann params anhaengen."""
    page_params = parse_qs(urlsplit(page_url).query)
    endpoint = _substitute(source.endpoint or "", page_params)
    if not source.params:
        return endpoint
    extra = urlencode(
        {
            _substitute(key, page_params): _substitute(value, page_params)
            for key, value in source.params.items()
        }
    )
    if not extra:
        return endpoint
    return f"{endpoint}{'&' if '?' in endpoint else '?'}{extra}"


def _is_json(content_type: str | None) -> bool:
    if not content_type:
        return False
    kind = content_type.split(";", 1)[0].strip().lower()
    return kind.endswith("+json") or kind in ("application/json", "text/json")


def _rows(payload: object, row_root: str | None) -> list[dict[str, object]]:
    """Die Zeilen unter ``row_root`` (bare Array: ``$[*]``); nur dict-Zeilen zaehlen."""
    if row_root:
        found = [match.value for match in jsonpath_parse(row_root).find(payload)]
    elif isinstance(payload, list):
        found = list(payload)
    else:
        found = [payload]
    return [row for row in found if isinstance(row, dict)]


class XhrExtractor:
    """Holt den AJAX-Endpunkt der Seite durch den Fetcher und bildet die Zeilen ab.

    ``fetcher`` ist der Lauf-Fetcher (robots/Rate/Retry erzwungen). ``snapshot``,
    wenn gesetzt, wird mit dem ``FetchResult`` gerufen, **bevor** irgendetwas geparst
    wird (ADR-009: Snapshot vor Parsing).
    """

    def __init__(
        self, fetcher: Fetcher, *, snapshot: Callable[[FetchResult], None] | None = None
    ) -> None:
        self._fetcher = fetcher
        self._snapshot = snapshot

    def extract_all(self, source: object, *, page_url: str) -> list[dict[str, object]]:
        assert isinstance(source, XhrSource)
        endpoint = _build_endpoint(source, page_url)
        try:
            result = self._fetcher.fetch(endpoint, headers=source.headers or None)
        except RobotsDenied:
            return []  # robots verbietet den Endpunkt -> null Requests, Miss
        # Snapshot VOR dem Parsen — auch fuer einen Miss (die Antwort ist Evidenz).
        if self._snapshot is not None:
            self._snapshot(result)
        if result.http_status != 200 or not _is_json(result.content_type):
            return []  # typisierter Fehltreffer -> die Kette faellt durch
        try:
            payload: object = json.loads(result.text)
        except json.JSONDecodeError:
            return []
        rows = _rows(payload, source.row_root)
        if not source.fields:
            return [dict(row) for row in rows]
        compiled = compile_field_map(source.fields)
        return [apply_field_map(compiled, row) for row in rows]

    def extract(self, source: object, *, page_url: str) -> dict[str, object]:
        """Die erste Zeile (Einzeilen-Sicht der Prioritaetskette)."""
        rows = self.extract_all(source, page_url=page_url)
        return dict(rows[0]) if rows else {}
