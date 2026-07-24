"""HttpFetcher: httpx-basierter Fetcher mit erzwungener Politeness.

Charset-Aufloesung und identifizierbarer User-Agent sitzen im Client, damit
keine Aufrufstelle sie umgehen kann. Robots-Pruefung (I0.7.3), Rate Limit
(I0.7.4), Retry (I0.7.5), bedingte Anfragen (I0.7.6) und Block-Erkennung
(I0.7.7) kommen additiv dazu.
"""

from __future__ import annotations

import re
import time

import httpx

from kintsugi.config import Settings, get_settings
from kintsugi.fetch.base import FetchOutcome, FetchResult
from kintsugi.fetch.robots import RobotsDenied, RobotsGate

_BROWSER_TOKENS = ("Mozilla", "Chrome", "Safari", "AppleWebKit", "Gecko")

# Meta-Charset in den ersten Bytes: <meta charset="..."> oder http-equiv.
_META_CHARSET = re.compile(rb"""charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE)
_HEADER_CHARSET = re.compile(r"""charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE)


def resolve_encoding(body: bytes, content_type: str | None) -> str:
    """Loest die Kodierung fest auf: BOM, dann Header, dann <meta>, dann UTF-8.

    **Kein latin-1-Fallback.** books.toscrape.com liefert UTF-8-Produktseiten
    OHNE charset-Parameter; httpx' eigener Default dekodierte `£51.77` als
    `Â£51.77`, was parse_currency und den payload_hash aus docs/03 zerstoert.
    """
    if body.startswith(b"\xef\xbb\xbf"):
        return "utf-8"
    if body.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if content_type:
        m = _HEADER_CHARSET.search(content_type)
        if m:
            return m.group(1).lower()
    head = body[:1024]
    m2 = _META_CHARSET.search(head)
    if m2:
        return m2.group(1).decode("ascii", "replace").lower()
    return "utf-8"


def _outcome_for(status: int) -> FetchOutcome:
    if status == 304:
        return FetchOutcome.not_modified
    if status == 404:
        return FetchOutcome.not_found
    if status == 429:
        return FetchOutcome.rate_limited
    if 200 <= status < 400:
        return FetchOutcome.ok
    return FetchOutcome.error


class HttpFetcher:
    """Ein wiederverwendeter httpx.Client mit erzwungenem User-Agent."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        respect_robots: bool = True,
    ) -> None:
        self._settings = settings or get_settings()
        user_agent = self._settings.user_agent  # ruft require_contact() -> wirft ohne Kontakt
        for token in _BROWSER_TOKENS:
            if token.lower() in user_agent.lower():
                raise ValueError(
                    f"User-Agent enthaelt Browser-Kennung {token!r} — Tarnung widerspricht "
                    "der Compliance-Zusage der README"
                )
        # Ein Client fuer den ganzen Lauf: ein Client pro Anfrage wirft
        # Connection-Pooling und HTTP/2 weg.
        self._client = httpx.Client(
            http2=True,
            timeout=httpx.Timeout(connect=5, read=self._settings.http_timeout_s, write=10, pool=5),
            follow_redirects=True,
            max_redirects=5,
            headers={"User-Agent": user_agent},
            transport=transport,
        )
        # Robots-Pruefung im Fetcher, damit keine Aufrufstelle sie umgeht. Nur
        # eine dokumentierte Ausnahme (RobotsOverride) setzt respect_robots=False.
        self._robots = RobotsGate(self._client, user_agent) if respect_robots else None

    @property
    def client(self) -> httpx.Client:
        return self._client

    def fetch(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResult:
        # robots.txt zuerst — auch fuer Discovery-URLs. RobotsUnavailable (5xx)
        # propagiert und bricht den Lauf ab; RobotsDenied ueberspringt die URL.
        if self._robots is not None and not self._robots.allowed(url):
            raise RobotsDenied(url)

        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        start = time.perf_counter()
        response = self._client.get(url, headers=headers)
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        body = response.content  # rohe Bytes, nie response.text
        content_type = response.headers.get("content-type")
        encoding = resolve_encoding(body, content_type)
        return FetchResult(
            url=url,
            final_url=str(response.url),
            http_status=response.status_code,
            headers=response.headers,
            body=body,
            content_type=content_type,
            encoding=encoding,
            elapsed_ms=elapsed_ms,
            fetcher="httpx",
            from_cache=False,
            outcome=_outcome_for(response.status_code),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
