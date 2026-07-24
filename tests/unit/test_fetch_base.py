"""Prueft das Fetcher-Protokoll und FetchResult (I0.7.1)."""

from __future__ import annotations

import dataclasses

import pytest
from kintsugi.fetch.base import Fetcher, FetchOutcome, FetchResult


def _result(**over: object) -> FetchResult:
    base: dict[str, object] = {
        "url": "https://x/a",
        "final_url": "https://x/a",
        "http_status": 200,
        "headers": {"Content-Type": "text/html"},
        "body": "GBP 51.77".encode("latin-1"),
        "content_type": "text/html",
        "encoding": "latin-1",
        "elapsed_ms": 12,
        "fetcher": "httpx",
        "from_cache": False,
        "outcome": FetchOutcome.ok,
    }
    base.update(over)
    return FetchResult(**base)  # type: ignore[arg-type]


def test_konstruierbar_aus_literalen_ohne_netz():
    r = _result()
    assert r.http_status == 200
    assert r.fetcher == "httpx"


def test_text_dekodiert_mit_gespeichertem_encoding():
    # 0xA3 ist das Pfundzeichen in latin-1; mit UTF-8 gedeutet waere es Muell.
    r = _result(body=b"\xa3" + b"51.77", encoding="latin-1")
    assert r.text == "£51.77"


def test_frozen_und_replace():
    r = _result()
    r2 = dataclasses.replace(r, body=b"neu")
    assert r2.body == b"neu"
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.body = b"x"  # type: ignore[misc]


def test_outcomes_sind_verschieden():
    assert FetchOutcome.rate_limited is not FetchOutcome.blocked
    assert FetchOutcome.rate_limited is not FetchOutcome.error
    assert FetchOutcome.blocked is not FetchOutcome.error
    for name in ("ok", "not_modified", "not_found", "rate_limited", "blocked", "error"):
        assert hasattr(FetchOutcome, name)


def test_dummy_erfuellt_das_protokoll_ohne_vererbung():
    class Dummy:
        def fetch(
            self, url: str, *, etag: str | None = None, last_modified: str | None = None
        ) -> FetchResult:
            return _result(url=url)

    assert isinstance(Dummy(), Fetcher)


def test_nicht_fetcher_erfuellt_das_protokoll_nicht():
    class Ohne:
        pass

    assert not isinstance(Ohne(), Fetcher)
