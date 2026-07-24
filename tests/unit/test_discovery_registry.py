"""Discovery-Registry, Stubs und seed_list (I0.9.4)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import get_args
from uuid import uuid4

import pytest
from kintsugi.discovery import REGISTRY, DiscoveryContext, DiscoveryStrategy, get_strategy
from kintsugi.discovery.seed_list import SeedListDiscovery
from kintsugi.packs.model import DiscoveryStrategyName
from kintsugi.quality.counters import RunCounters


def _ctx() -> DiscoveryContext:
    # seed_list ruft weder fetcher noch run_id auf; ein Platzhalter genuegt.
    return DiscoveryContext(fetcher=SimpleNamespace(), run_id=uuid4(), counters=RunCounters())  # type: ignore[arg-type]


def _pack(*, seeds, url_pattern=None, max_urls=1000):
    disc = SimpleNamespace(seeds=seeds, url_pattern=url_pattern, max_urls_per_run=max_urls)
    return SimpleNamespace(discovery=disc)


def test_registry_deckt_sich_mit_schema_literal():
    assert set(REGISTRY) == set(get_args(DiscoveryStrategyName))


@pytest.mark.parametrize("name", ["sitemap", "api"])
def test_stub_strategien_scheitern_mit_phasennennung(name):
    with pytest.raises(NotImplementedError, match="Phase"):
        list(get_strategy(name).discover(_pack(seeds=["x"]), _ctx()))


def test_seed_list_reihenfolge_dedup_und_pattern():
    pack = _pack(
        seeds=[
            "https://x/a",
            "https://x/a",  # exaktes Duplikat -> raus
            "https://y/b",  # passt nicht auf pattern -> raus
            "https://x/c",
        ],
        url_pattern=r"^https://x/",
    )
    ctx = _ctx()
    assert list(SeedListDiscovery().discover(pack, ctx)) == ["https://x/a", "https://x/c"]
    assert ctx.counters.urls_discovered == 2


def test_seed_list_kappt_bei_max_urls_per_run():
    pack = _pack(seeds=[f"https://x/{i}" for i in range(10)], max_urls=3)
    assert list(SeedListDiscovery().discover(pack, _ctx())) == [
        "https://x/0",
        "https://x/1",
        "https://x/2",
    ]


def test_protokoll_konformitaet():
    assert isinstance(SeedListDiscovery(), DiscoveryStrategy)

    class OhneDiscover:
        pass

    assert not isinstance(OhneDiscover(), DiscoveryStrategy)
