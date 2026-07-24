"""Prueft die bedingten Anfragen der Fetch-Schicht (I0.7.6).

Die 304-Snapshot-Zeile (content_hash/blob_key nachtragen, kein Blob schreiben,
Extraktion ueberspringen, dangling-304-Retry) haengt am Schreibpfad und wird in
E0.9 verdrahtet und getestet. Hier: die Header und das from_cache-Verhalten.
"""

from __future__ import annotations

import httpx
import pytest
from kintsugi.config import Settings
from kintsugi.fetch.base import FetchOutcome
from kintsugi.fetch.http import HttpFetcher

CONTACT = "ops@example.invalid"


def _fetcher(handler) -> HttpFetcher:
    return HttpFetcher(
        Settings(contact=CONTACT), transport=httpx.MockTransport(handler), respect_robots=False
    )


def test_etag_wird_als_if_none_match_gesendet():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(304)

    with _fetcher(handler) as f:
        f.fetch("https://x/a", etag='"abc"')
    assert seen["if-none-match"] == '"abc"'


def test_last_modified_wird_als_if_modified_since_gesendet():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(304)

    with _fetcher(handler) as f:
        f.fetch("https://x/a", last_modified="Mon, 21 Jul 2026 10:00:00 GMT")
    assert seen["if-modified-since"].startswith("Mon")


def test_304_ist_not_modified_und_from_cache():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    with _fetcher(handler) as f:
        result = f.fetch("https://x/a", etag='"abc"')
    assert result.outcome is FetchOutcome.not_modified
    assert result.from_cache is True


def test_ohne_validatoren_werden_keine_conditional_header_gesendet():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, content=b"x")

    with _fetcher(handler) as f:
        f.fetch("https://x/a")
    assert "if-none-match" not in seen
    assert "if-modified-since" not in seen


@pytest.mark.parametrize("header", ["ETag", "Last-Modified"])
def test_200_traegt_die_validatoren_in_den_headern(header):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={header: "xyz"}, content=b"x")

    with _fetcher(handler) as f:
        result = f.fetch("https://x/a")
    assert result.headers.get(header) == "xyz"
