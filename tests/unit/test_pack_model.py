"""Prueft den SitePack-Kern, DiscoverySpec und FetchSpec (I0.6.1)."""

from __future__ import annotations

import pytest
from kintsugi.packs.model import DiscoverySpec, FetchSpec, SitePack
from pydantic import ValidationError


def _minimal_pack(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "apiVersion": "kintsugi/v1",
        "domain": "books.toscrape.com",
        "entity": "book",
        "version": 1,
        "discovery": {
            "strategy": "pagination",
            "url_template": "https://books.toscrape.com/catalogue/page-{n}.html",
            "page_stop": 50,
        },
        "extract": {
            "sources": [
                {"kind": "css", "fields": {"title": {"selector": "h1"}}},
            ],
        },
        "schema": {
            "natural_key": ["upc"],
            "fields": {"upc": {"type": "string", "required": True}},
        },
    }
    base.update(overrides)
    return base


def test_gueltiges_minimalpack():
    pack = SitePack.model_validate(_minimal_pack())
    assert pack.domain == "books.toscrape.com"
    assert pack.discovery.strategy == "pagination"


def test_unbekannter_top_level_key_wird_abgelehnt():
    with pytest.raises(ValidationError):
        SitePack.model_validate(_minimal_pack(unbekannt="x"))


def test_falsche_api_version_wird_abgelehnt():
    with pytest.raises(ValidationError):
        SitePack.model_validate(_minimal_pack(apiVersion="kintsugi/v2"))


def test_version_muss_mindestens_eins_sein():
    with pytest.raises(ValidationError):
        SitePack.model_validate(_minimal_pack(version=0))


def test_pack_ist_unveraenderlich():
    pack = SitePack.model_validate(_minimal_pack())
    with pytest.raises(ValidationError):
        pack.domain = "andere.domain"  # type: ignore[misc]


# --------------------------------------------------------------------------
# DiscoverySpec
# --------------------------------------------------------------------------


def test_pagination_ohne_url_template_wird_abgelehnt():
    with pytest.raises(ValidationError, match="url_template"):
        DiscoverySpec(strategy="pagination")


def test_pagination_ohne_n_platzhalter_wird_abgelehnt():
    with pytest.raises(ValidationError, match=r"\{n\}"):
        DiscoverySpec(strategy="pagination", url_template="https://x/page.html")


def test_sitemap_ohne_url_wird_abgelehnt():
    with pytest.raises(ValidationError, match="sitemap_url"):
        DiscoverySpec(strategy="sitemap")


def test_seed_list_ohne_seeds_wird_abgelehnt():
    with pytest.raises(ValidationError, match="seeds"):
        DiscoverySpec(strategy="seed_list")


def test_kaputtes_url_pattern_wird_abgelehnt():
    with pytest.raises(ValidationError, match="regulaerer Ausdruck"):
        DiscoverySpec(strategy="pagination", url_template="p-{n}", url_pattern="(unbalanced")


# --------------------------------------------------------------------------
# FetchSpec
# --------------------------------------------------------------------------


def test_fetch_defaults():
    f = FetchSpec()
    assert f.rate_limit_rps == 0.5
    assert f.concurrency == 2
    assert f.respect_robots is True
    assert f.conditional_requests is True
    assert f.proxy_pool is None
    assert f.strategy == "http"


def test_fetch_negative_rate_wird_abgelehnt():
    with pytest.raises(ValidationError):
        FetchSpec(rate_limit_rps=0)
