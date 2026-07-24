"""Zulassungspruefung: jede Eintritts-URL eines Packs muss robots-erlaubt sein (I1.5.1).

docs/07 §Stufe 2/§Stufe 4 („ToS und robots.txt vorher pruefen"), docs/08 Phase 1,
README §Compliance („robots.txt respected by default and not disablable"). Fuer jede
gebotene Quelle wird die robots.txt mit dem Projekt-User-Agent geholt, mit **protego**
geparst (ueber das wiederverwendete ``RobotsGate`` aus I0.3.2 — keine zweite
Implementierung) und geprueft, dass jede in ``packs/**/*.yaml`` deklarierte
Eintritts-URL erlaubt ist. **F1** gilt unveraendert: eine robots.txt mit 404 heisst
allow-all (RFC 9309 §2.3.1.3).

**F4** ist der Grund fuer dieses Werkzeug: ``webscraper.io/robots.txt`` verbietet
``/test-sites/e-commerce/``, weshalb die urspruenglich in docs/08 genannte AJAX-
Sandbox unzulaessig ist und durch ``scrapethissite.com/pages/ajax-javascript/``
ersetzt wurde. ``--offline`` fuehrt die Pruefung ohne Netz gegen aufgezeichnete
robots-Fixtures (``tools/robots_fixtures.json``) — genau so laeuft sie im
CI-Push-Job; ein live-markierter Zeitplan-Job prueft dieselben Quellen echt.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import urlsplit

import httpx
import typer
from kintsugi.config import ConfigError, Settings, get_settings
from kintsugi.fetch.robots import RobotsGate
from kintsugi.packs.loader import load_packs

if TYPE_CHECKING:
    from kintsugi.packs.model import SitePack

_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = Path(__file__).resolve().parent / "robots_fixtures.json"

app = typer.Typer(add_completion=False)


@dataclass(frozen=True)
class Verdict:
    """Das Urteil zu einer Quelle: erlaubt oder nicht, mit Begruendung."""

    label: str
    url: str
    allowed: bool
    reason: str


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _user_agent(settings: Settings) -> str:
    # Die robots-Fixtures gruppieren auf ``User-agent: *``, der genaue String ist
    # fuer das Matching also unerheblich; ohne KINTSUGI_CONTACT ein neutraler UA,
    # damit ``--offline`` auch ohne gesetzten Kontakt laeuft.
    try:
        return settings.user_agent
    except ConfigError:
        return "kintsugi-admission-check"


def _load_fixtures() -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = json.loads(_FIXTURES.read_text("utf-8"))
    return data


def _offline_gate(settings: Settings, fixtures: dict[str, dict[str, Any]]) -> RobotsGate:
    def handler(request: httpx.Request) -> httpx.Response:
        origin = f"{request.url.scheme}://{request.url.host}"
        entry = fixtures.get(origin)
        if entry is None:
            return httpx.Response(404)  # unbekannter Origin -> 404 -> allow-all (F1)
        return httpx.Response(int(entry["status"]), text=str(entry.get("body", "")))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return RobotsGate(client, _user_agent(settings))


def _live_gate(settings: Settings) -> RobotsGate:
    ua = _user_agent(settings)
    client = httpx.Client(follow_redirects=True, headers={"User-Agent": ua})
    return RobotsGate(client, ua)


def _disallow_line(body: str, url: str) -> str | None:
    """Die erste ``Disallow``-Zeile, deren Pfad Praefix der URL ist (fuer die Meldung)."""
    path = urlsplit(url).path or "/"
    for raw in body.splitlines():
        line = raw.strip()
        if line.lower().startswith("disallow:"):
            rule = line.split(":", 1)[1].strip()
            if rule and path.startswith(rule):
                return line
    return None


def _entry_urls(pack: SitePack) -> list[str]:
    """Die Eintritts-URLs eines Packs: Discovery-Seeds oder die erste Pagination-Seite."""
    disc = pack.discovery
    if disc.seeds:
        return list(disc.seeds)
    if disc.url_template:
        return [disc.url_template.replace("{n}", str(disc.page_start))]
    return []


def all_entry_urls() -> list[str]:
    """Alle Pack-Eintritts-URLs flach (fuer die Pruefung und den docs-Guard-Test)."""
    return [url for pack in load_packs(root=_ROOT / "packs") for url in _entry_urls(pack)]


def _verdict(
    label: str,
    urls: list[str],
    gate: RobotsGate,
    fixtures: dict[str, dict[str, Any]],
    *,
    offline: bool,
) -> Verdict:
    for url in urls:
        if not gate.allowed(url):
            body = str(fixtures.get(_origin(url), {}).get("body", "")) if offline else ""
            line = _disallow_line(body, url)
            return Verdict(label, url, False, f"disallowed: {line}" if line else "disallowed")
    first = urls[0] if urls else ""
    status = int(fixtures.get(_origin(first), {}).get("status", 404)) if offline else 200
    reason = "allowed (no robots.txt, RFC 9309 2.3.1.3)" if status in (404, 410) else "allowed"
    return Verdict(label, first, True, reason)


def check(
    *,
    offline: bool,
    inject: list[str] | None = None,
    settings: Settings | None = None,
) -> tuple[int, list[Verdict]]:
    """Prueft alle Pack-Eintritts-URLs (plus injizierte) — ein Urteil je Quelle."""
    settings = settings or get_settings()
    fixtures = _load_fixtures() if offline else {}
    gate = _offline_gate(settings, fixtures) if offline else _live_gate(settings)

    verdicts: list[Verdict] = []
    for pack in load_packs(root=_ROOT / "packs"):
        verdicts.append(
            _verdict(
                f"{pack.domain}/{pack.entity}", _entry_urls(pack), gate, fixtures, offline=offline
            )
        )
    for url in inject or []:
        verdicts.append(_verdict("(injected)", [url], gate, fixtures, offline=offline))

    exit_code = 0 if all(v.allowed for v in verdicts) else 1
    return exit_code, verdicts


@app.command()
def main(
    offline: Annotated[
        bool, typer.Option("--offline", help="Gegen aufgezeichnete robots-Fixtures.")
    ] = False,
    inject: Annotated[
        list[str] | None, typer.Option("--inject", help="Zusaetzliche URL(s) pruefen (Test).")
    ] = None,
) -> None:
    """Druckt ein Urteil je Quelle und beendet mit 1, wenn eine URL verboten ist."""
    exit_code, verdicts = check(offline=offline, inject=list(inject or []))
    for verdict in verdicts:
        mark = "OK  " if verdict.allowed else "XX  "
        typer.echo(f"{mark}{verdict.label:34} {verdict.url}  ->  {verdict.reason}")
    if exit_code != 0:
        typer.echo("Zulassungspruefung fehlgeschlagen: mindestens eine Eintritts-URL ist verboten.")
    raise typer.Exit(exit_code)


if __name__ == "__main__":
    sys.exit(app())  # pragma: no cover
