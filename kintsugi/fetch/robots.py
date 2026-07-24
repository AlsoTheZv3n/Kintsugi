"""robots.txt-Policy nach RFC 9309.

Jede von ``HttpFetcher`` ausgeloeste Anfrage — auch Discovery-Seiten, nicht nur
Detailseiten — wird zuerst geprueft. Die Pruefung sitzt im Fetcher, keine
Aufrufstelle kann sie umgehen. Geparst wird mit protego (dem Parser, den Scrapy
nutzt), nicht mit stdlib-robotparser, das kein Crawl-delay kennt.

Fehlermodi (RFC 9309 §2.3.1):
  2xx                 -> parsen und anwenden
  404 / 410           -> allow all  (F1: books.toscrape.com liefert 404)
  401 / 403           -> ganze Domain sperren
  5xx / Timeout       -> Domain sperren, Lauf mit robots_unavailable abbrechen
  Redirect            -> bis 5 Hops folgen (httpx), dann unerreichbar
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import httpx
from protego import Protego

_CACHE_TTL_S = 24 * 60 * 60

_ALLOW_ALL = Protego.parse("")
_DENY_ALL = Protego.parse("User-agent: *\nDisallow: /\n")


class RobotsUnavailable(Exception):
    """robots.txt ist unerreichbar (5xx/Timeout) — der Lauf bricht ab."""

    reason = "robots_unavailable"


class RobotsDenied(Exception):
    """robots.txt verbietet diese URL — die Zeile wird uebersprungen, nicht geholt."""


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class RobotsGate:
    """Prueft URLs gegen die robots.txt ihres Origins; cached pro Origin 24 h."""

    def __init__(self, client: httpx.Client, user_agent: str) -> None:
        self._client = client
        self._user_agent = user_agent
        self._cache: dict[str, tuple[float, Protego]] = {}

    def _policy_for(self, url: str) -> Protego:
        origin = _origin(url)
        cached = self._cache.get(origin)
        if cached is not None and (time.monotonic() - cached[0]) < _CACHE_TTL_S:
            return cached[1]
        policy = self._fetch_policy(origin)
        self._cache[origin] = (time.monotonic(), policy)
        return policy

    def _fetch_policy(self, origin: str) -> Protego:
        try:
            response = self._client.get(f"{origin}/robots.txt")
        except httpx.HTTPError as exc:  # Timeout, ConnectError, TooManyRedirects
            raise RobotsUnavailable(f"{origin}/robots.txt unerreichbar: {exc}") from exc

        status = response.status_code
        if status in (404, 410):
            return _ALLOW_ALL  # F1: keine robots.txt bedeutet erlaubt
        if status in (401, 403):
            return _DENY_ALL
        if status >= 500:
            raise RobotsUnavailable(f"{origin}/robots.txt lieferte {status}")
        if 200 <= status < 300:
            return Protego.parse(response.text)
        # Unerwarteter Status: konservativ sperren.
        return _DENY_ALL

    def allowed(self, url: str) -> bool:
        """True, wenn der User-Agent die URL abrufen darf. Wirft bei 5xx/Timeout."""
        return bool(self._policy_for(url).can_fetch(url, self._user_agent))

    def crawl_delay(self, url: str) -> float | None:
        """Crawl-delay der robots.txt fuer unsere UA-Gruppe, falls gesetzt."""
        delay = self._policy_for(url).crawl_delay(self._user_agent)
        return float(delay) if delay is not None else None
