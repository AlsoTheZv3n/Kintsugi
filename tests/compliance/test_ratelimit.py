"""Prueft den Token-Bucket und die Concurrency-Grenze (I0.7.4)."""

from __future__ import annotations

import threading

import pytest
from kintsugi.fetch.ratelimit import (
    DomainLimiter,
    TokenBucket,
    get_limiter,
    registrable_domain,
    reset_limiters,
)


class FakeClock:
    """Virtuelle Uhr: sleep() schiebt die Zeit vor, kein Wanduhr-Warten."""

    def __init__(self) -> None:
        self.now = 0.0
        self._lock = threading.Lock()

    def time(self) -> float:
        return self.now

    def sleep(self, dt: float) -> None:
        with self._lock:
            self.now += dt


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_limiters()
    yield
    reset_limiters()


def test_zehn_token_bei_rps_0_5_brauchen_18_virtuelle_sekunden():
    clock = FakeClock()
    bucket = TokenBucket(0.5, clock=clock.time, sleep=clock.sleep)
    for _ in range(10):
        bucket.acquire()
    # Erster Token sofort, dann 9 * 2 s.
    assert clock.now >= 18.0


def test_registrierbare_domain_ignoriert_www():
    assert registrable_domain("https://www.books.toscrape.com/x") == "books.toscrape.com"
    assert registrable_domain("books.toscrape.com") == "books.toscrape.com"


def test_zwei_packs_gleicher_domain_teilen_den_limiter():
    a = get_limiter("books.toscrape.com", 0.5, 2)
    b = get_limiter("www.books.toscrape.com", 0.5, 2)
    assert a is b  # ein Budget je Domain, keine Verdopplung


def test_crawl_delay_hebt_das_intervall_an():
    b1 = TokenBucket(2.0)  # base 0.5 s
    b1.raise_crawl_delay(5.0)
    assert b1.effective_interval == 5.0

    b2 = TokenBucket(0.1)  # base 10 s
    b2.raise_crawl_delay(1.0)  # darf nicht senken
    assert b2.effective_interval == 10.0


def test_concurrency_begrenzt_gleichzeitige_anfragen():
    clock = FakeClock()
    limiter = DomainLimiter(0.5, 2, clock=clock.time, sleep=clock.sleep)
    in_flight = 0
    max_in_flight = 0
    lock = threading.Lock()
    barrier = threading.Barrier(1)  # kein echtes Warten noetig

    def worker() -> None:
        nonlocal in_flight, max_in_flight
        with limiter.slot():
            with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            with lock:
                in_flight -= 1

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _ = barrier
    assert max_in_flight <= 2
    assert clock.now >= 6.0  # 4 Anfragen bei 0.5 rps: 0, 2, 4, 6
