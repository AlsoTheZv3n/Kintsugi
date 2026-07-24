"""Token-Bucket pro Domain und Concurrency-Grenze (docs/01 §Fetch, Rate Limiting).

**Der Schluessel ist die Entwurfsentscheidung.** Die Limiter-Registry ist
prozessweit und keyed auf die registrierbare Domain, NIE auf das Site-Pack.
docs/02 deklariert ein Pack pro Domain UND Entitaet, also bekaemen
``books.toscrape.com/book`` und ein kuenftiges ``books.toscrape.com/category``
sonst je ein eigenes Budget und verdoppelten still die Last auf einem Server.
Das ``www.``-Praefix wird abgeschnitten.

Token-Bucket: ``rate_limit_rps`` ist ein globales Budget der Domain, Kapazitaet
1 Token (kein Burst in Phase 0 — genau den bemerkt ein Sandbox-Betreiber). Das
effektive Mindestintervall ist ``max(1/rps, crawl_delay)``: ein per robots
deklariertes Crawl-delay darf das Intervall nur anheben, nie senken.

Concurrency ist eine Semaphore, die nur gleichzeitige Anfragen begrenzt — sie
multipliziert den Durchsatz nicht, weil jeder Worker vor der Anfrage einen Token
nehmen muss. Uhr und Sleep sind injizierbar, damit Tests in Mikrosekunden statt
Wanduhr-Sekunden laufen.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit


def registrable_domain(host_or_url: str) -> str:
    """Die Domain, auf die der Limiter keyed — Host ohne ``www.``."""
    host = urlsplit(host_or_url).hostname if "//" in host_or_url else host_or_url
    host = (host or host_or_url).lower().strip()
    return host[4:] if host.startswith("www.") else host


class TokenBucket:
    """Ein Token je effektivem Intervall, threadsicher, mit injizierbarer Uhr."""

    def __init__(
        self,
        rps: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_interval = 1.0 / rps if rps > 0 else 0.0
        self._crawl_delay = 0.0
        self._clock = clock
        self._sleep = sleep
        self._next_free: float | None = None
        self._lock = threading.Lock()

    @property
    def effective_interval(self) -> float:
        return max(self._base_interval, self._crawl_delay)

    def raise_crawl_delay(self, delay: float | None) -> None:
        """Ein robots-Crawl-delay hebt das Intervall an, senkt es nie."""
        if delay is not None and delay > self._crawl_delay:
            self._crawl_delay = delay

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            interval = self.effective_interval
            if self._next_free is None or now >= self._next_free:
                self._next_free = now + interval
                return
            wait = self._next_free - now
            self._sleep(wait)
            self._next_free = self._next_free + interval


class DomainLimiter:
    """Token-Bucket plus Concurrency-Semaphore fuer eine Domain."""

    def __init__(
        self,
        rps: float,
        concurrency: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.bucket = TokenBucket(rps, clock=clock, sleep=sleep)
        self.semaphore = threading.Semaphore(concurrency)

    @contextmanager
    def slot(self) -> Iterator[None]:
        """Belegt einen Concurrency-Platz und nimmt dann einen Rate-Token."""
        with self.semaphore:
            self.bucket.acquire()
            yield


_REGISTRY: dict[str, DomainLimiter] = {}
_REGISTRY_LOCK = threading.Lock()


def get_limiter(
    domain: str,
    rps: float,
    concurrency: int,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> DomainLimiter:
    """Der Limiter fuer die registrierbare Domain — dieselbe Instanz je Domain."""
    key = registrable_domain(domain)
    with _REGISTRY_LOCK:
        limiter = _REGISTRY.get(key)
        if limiter is None:
            limiter = DomainLimiter(rps, concurrency, clock=clock, sleep=sleep)
            _REGISTRY[key] = limiter
        return limiter


def reset_limiters() -> None:
    """Leert die Registry (fuer Tests)."""
    with _REGISTRY_LOCK:
        _REGISTRY.clear()
