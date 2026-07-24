"""pagination-Discovery: 404-Terminierung, Klemmschutz, Cap, Politeness (I0.9.5)."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from kintsugi.discovery import DiscoveryContext, get_strategy
from kintsugi.discovery.pagination import PaginationDiscovery
from kintsugi.fetch.base import FetchOutcome, FetchResult
from kintsugi.fetch.http import HttpFetcher
from kintsugi.fetch.ratelimit import DomainLimiter
from kintsugi.packs.loader import load_pack
from kintsugi.quality.counters import RunCounters

CONTACT = "kintsugi-bot (+mailto:ops@example.com)"
LINK_SELECTOR = "article.product_pod h3 a"


class SpyFetcher:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls: list[str] = []

    def fetch(self, url, *, etag=None, last_modified=None):
        self.calls.append(url)
        return self.inner.fetch(url, etag=etag, last_modified=last_modified)


def _ctx(fetcher) -> DiscoveryContext:
    return DiscoveryContext(fetcher=fetcher, run_id=uuid4(), counters=RunCounters())


def _result(url, *, status=200, body=b"") -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        http_status=status,
        headers={},
        body=body,
        content_type="text/html",
        encoding="utf-8",
        elapsed_ms=1,
        fetcher="httpx",
        from_cache=False,
        outcome=FetchOutcome.ok if 200 <= status < 300 else FetchOutcome.not_found,
    )


class FakeFetcher:
    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def fetch(self, url, *, etag=None, last_modified=None):
        self.calls.append(url)
        body = self.pages.get(url)
        if body is None:
            return _result(url, status=404, body=b"not found")
        return _result(url, status=200, body=body)


def _index_html(hrefs: list[str]) -> bytes:
    pods = "".join(
        f'<article class="product_pod"><h3><a href="{h}">t</a></h3></article>' for h in hrefs
    )
    return f"<html><body>{pods}</body></html>".encode()


def _fake_pack(base: str) -> SimpleNamespace:
    disc = SimpleNamespace(
        url_template=f"{base}/catalogue/page-{{n}}.html",
        url_pattern=r"/catalogue/[^/]+/index\.html$",
        link_selector=LINK_SELECTOR,
        page_start=1,
        page_stop=50,
        max_urls_per_run=1000,
    )
    return SimpleNamespace(discovery=disc)


def _books_pack_on(base: str):
    pack = load_pack("books.toscrape.com", "book", root=Path("packs"))
    disc = pack.discovery.model_copy(
        update={
            "url_template": f"{base}/catalogue/page-{{n}}.html",
            "url_pattern": r"^http://127\.0\.0\.1:\d+/catalogue/[^/]+/index\.html$",
        }
    )
    return pack.model_copy(update={"discovery": disc})


def test_registry_liefert_die_echte_pagination_strategie():
    # Regressionswaechter: der Phase-0-Stub darf die echte Strategie nicht
    # verdraengen, egal wie das Paket seine Module sortiert importiert.
    assert isinstance(get_strategy("pagination"), PaginationDiscovery)


def test_voller_walk_gegen_fixture_server(books_fixture_base_url):
    from kintsugi.config import Settings

    pack = _books_pack_on(books_fixture_base_url)
    # Echter HttpFetcher (robots via 404-allow, Limiter aktiv aber schnell).
    fetcher = SpyFetcher(
        HttpFetcher(
            Settings(contact=CONTACT), limiter=DomainLimiter(1000.0, 2), respect_robots=True
        )
    )
    ctx = _ctx(fetcher)
    urls = list(PaginationDiscovery().discover(pack, ctx))

    assert len(urls) == 240
    assert len(set(urls)) == 240  # jede eindeutig, 20 je Seite ueber 12 Seiten
    assert all(re.search(r"/catalogue/[^/]+/index\.html$", u) for u in urls)
    assert all(u.startswith(books_fixture_base_url) for u in urls)  # relativ -> absolut
    # 12 Index-Seiten + die 404-Terminatorseite (page-13), genau einmal geholt.
    assert fetcher.calls[-1].endswith("/page-13.html")
    assert sum(1 for c in fetcher.calls if c.endswith("/page-13.html")) == 1
    assert ctx.counters.pages_fetched == 13


def test_max_urls_kappt_und_holt_hoechstens_zwei_seiten(books_fixture_base_url):
    from kintsugi.config import Settings

    pack = _books_pack_on(books_fixture_base_url)
    pack = pack.model_copy(
        update={"discovery": pack.discovery.model_copy(update={"max_urls_per_run": 30})}
    )
    fetcher = SpyFetcher(
        HttpFetcher(
            Settings(contact=CONTACT), limiter=DomainLimiter(1000.0, 2), respect_robots=True
        )
    )
    ctx = _ctx(fetcher)
    urls = list(PaginationDiscovery().discover(pack, ctx))
    assert len(urls) == 30
    assert len(fetcher.calls) <= 2


def test_seite_ohne_neue_links_terminiert():
    base = "http://fake"
    idx = _index_html(["a-1_1/index.html", "b-2_2/index.html"])
    pages = {
        f"{base}/catalogue/page-1.html": idx,
        f"{base}/catalogue/page-2.html": idx,  # exakt dieselben Links
    }
    fake = FakeFetcher(pages)
    ctx = _ctx(fake)
    urls = list(PaginationDiscovery().discover(_fake_pack(base), ctx))
    assert urls == [
        f"{base}/catalogue/a-1_1/index.html",
        f"{base}/catalogue/b-2_2/index.html",
    ]
    # page-2 wurde geholt, lieferte null neue -> Stopp (kein Endloslauf).
    assert fake.calls == [f"{base}/catalogue/page-1.html", f"{base}/catalogue/page-2.html"]


def test_relative_hrefs_werden_vor_dem_filter_aufgeloest():
    base = "http://fake"
    # Ein Link passt erst nach urljoin auf das Muster.
    pages = {f"{base}/catalogue/page-1.html": _index_html(["deep-slug_7/index.html"])}
    fake = FakeFetcher(pages)
    ctx = _ctx(fake)
    urls = list(PaginationDiscovery().discover(_fake_pack(base), ctx))
    assert urls == [f"{base}/catalogue/deep-slug_7/index.html"]
