"""Prueft HttpFetcher: Charset-Aufloesung und Client-Wiederverwendung (I0.7.2)."""

from __future__ import annotations

import httpx
import pytest
from kintsugi.config import Settings
from kintsugi.fetch.http import HttpFetcher, resolve_encoding

CONTACT = "ops@example.invalid"


def _settings(**over: object) -> Settings:
    return Settings(contact=CONTACT, **over)


def _fetcher(handler) -> HttpFetcher:
    return HttpFetcher(_settings(), transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------
# Charset-Aufloesung
# --------------------------------------------------------------------------


def test_utf8_ohne_charset_header_wird_nicht_zu_latin1():
    """Live: books.toscrape.com liefert UTF-8 ohne charset."""
    body = "Preis £51.77".encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=body)

    with _fetcher(handler) as f:
        result = f.fetch("https://x/a")
    assert "£51.77" in result.text
    assert "Â£51.77" not in result.text


@pytest.mark.parametrize(
    ("content_type", "body", "expected"),
    [
        ("text/html; charset=iso-8859-1", b"\xa3", "iso-8859-1"),  # Header gewinnt
        ("text/html", b'<meta charset="utf-8">x', "utf-8"),  # meta gewinnt ueber Default
        ("text/html", b"nix", "utf-8"),  # Default
    ],
)
def test_charset_praezedenz(content_type, body, expected):
    assert resolve_encoding(body, content_type) == expected


def test_bom_gewinnt_ueber_header():
    assert resolve_encoding(b"\xef\xbb\xbfx", "text/html; charset=iso-8859-1") == "utf-8"


# --------------------------------------------------------------------------
# Client-Wiederverwendung und Outcome
# --------------------------------------------------------------------------


def test_derselbe_client_ueber_zwei_fetches():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x")

    with _fetcher(handler) as f:
        c1 = f.client
        f.fetch("https://x/a")
        f.fetch("https://x/b")
        assert f.client is c1
        assert f.client.timeout.read is not None


@pytest.mark.parametrize(
    ("status", "outcome"),
    [(200, "ok"), (304, "not_modified"), (404, "not_found"), (429, "rate_limited"), (500, "error")],
)
def test_outcome_mapping(status, outcome):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=b"x")

    with _fetcher(handler) as f:
        assert f.fetch("https://x/a").outcome.value == outcome


def test_conditional_header_wird_gesetzt():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(304, content=b"")

    with _fetcher(handler) as f:
        f.fetch("https://x/a", etag='"abc"', last_modified="Mon, 21 Jul 2026 10:00:00 GMT")
    assert seen["if-none-match"] == '"abc"'
    assert seen["if-modified-since"].startswith("Mon")
