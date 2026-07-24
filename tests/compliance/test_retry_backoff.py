"""Prueft Retry mit Backoff und das eigene rate_limited-Outcome (I0.7.5, N04)."""

from __future__ import annotations

import httpx
import pytest
from kintsugi.config import Settings
from kintsugi.fetch.base import FetchOutcome
from kintsugi.fetch.http import HttpFetcher, parse_retry_after
from kintsugi.fetch.ratelimit import DomainLimiter, reset_limiters

CONTACT = "ops@example.invalid"


class _Counter:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = responses
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        r = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return r


def _fetcher(handler, **over) -> HttpFetcher:
    slept: list[float] = []
    over.setdefault("sleep", slept.append)
    f = HttpFetcher(
        Settings(contact=CONTACT),
        transport=httpx.MockTransport(handler),
        respect_robots=False,
        **over,
    )
    f.slept = slept  # type: ignore[attr-defined]
    return f


@pytest.fixture(autouse=True)
def _clean():
    reset_limiters()
    yield
    reset_limiters()


def test_503_503_200_gelingt_nach_drei_calls():
    counter = _Counter(
        [httpx.Response(503), httpx.Response(503), httpx.Response(200, content=b"ok")]
    )
    with _fetcher(counter) as f:
        result = f.fetch("https://x/a")
    assert counter.calls == 3
    assert result.outcome is FetchOutcome.ok


def test_vier_mal_503_gibt_error_nach_drei_calls():
    counter = _Counter([httpx.Response(503)])
    with _fetcher(counter) as f:
        result = f.fetch("https://x/a")
    assert counter.calls == 3  # stop_after_attempt(3)
    assert result.outcome is FetchOutcome.error


@pytest.mark.parametrize("status", [404, 400])
def test_nicht_transiente_status_werden_nicht_retried(status):
    counter = _Counter([httpx.Response(status)])
    with _fetcher(counter) as f:
        f.fetch("https://x/a")
    assert counter.calls == 1


def test_429_ehrt_retry_after_und_meldet_rate_limited():
    counter = _Counter([httpx.Response(429, headers={"Retry-After": "7"})])
    with _fetcher(counter) as f:
        result = f.fetch("https://x/a")
    assert result.outcome is FetchOutcome.rate_limited
    assert result.outcome is not FetchOutcome.error
    assert max(f.slept) >= 7.0  # type: ignore[attr-defined]


def test_429_setzt_domain_weite_abkuehlung():
    limiter = DomainLimiter(2.0, 2)
    counter = _Counter([httpx.Response(429, headers={"Retry-After": "9"})])
    with _fetcher(counter, limiter=limiter) as f:
        f.fetch("https://x/a")
    # Andere Worker auf derselben Domain sehen jetzt ein groesseres Intervall.
    assert limiter.bucket.effective_interval >= 9.0


def test_parse_retry_after_formen():
    assert parse_retry_after("7", now=0) == 7.0
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT", now=0) is not None
    assert parse_retry_after(None, now=0) is None
    assert parse_retry_after("garbage", now=0) is None
