"""Prueft den identifizierbaren User-Agent und die Browser-Tarnung-Sperre (I0.7.2)."""

from __future__ import annotations

import httpx
import pytest
from kintsugi.config import ConfigError, Settings
from kintsugi.fetch.http import HttpFetcher

CONTACT = "ops@example.invalid"


def test_jede_anfrage_traegt_den_kontakt():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, content=b"x")

    with HttpFetcher(
        Settings(contact=CONTACT), transport=httpx.MockTransport(handler), respect_robots=False
    ) as f:
        f.fetch("https://x/a")
    ua = seen["user-agent"]
    assert ua.startswith("kintsugi/")
    assert CONTACT in ua


def test_ohne_kontakt_scheitert_die_konstruktion():
    with pytest.raises(ConfigError):
        HttpFetcher(Settings(contact=None))


@pytest.mark.parametrize("token", ["Mozilla", "Chrome", "Safari", "AppleWebKit"])
def test_browser_tarnung_wird_abgelehnt(token):
    with pytest.raises(ValueError, match="Browser-Kennung"):
        HttpFetcher(Settings(contact=CONTACT, user_agent_product=f"{token}/5.0"))
