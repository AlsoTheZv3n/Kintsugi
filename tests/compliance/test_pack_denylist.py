"""Prueft die Domain-Denylist und die Zugangsdaten-Sperre (I0.6.5)."""

from __future__ import annotations

import pytest
from kintsugi.packs.denylist import (
    DENIED_BRANDS,
    CredentialInPackError,
    DeniedTargetError,
    check_domain,
    check_no_credentials,
)
from kintsugi.packs.model import SitePack
from pydantic import ValidationError


@pytest.mark.parametrize(
    "domain", ["amazon.de", "www.linkedin.com", "de.linkedin.com", "INSTAGRAM.com"]
)
def test_verbotene_domains_werfen(domain):
    with pytest.raises(DeniedTargetError, match="Nicht anfassen"):
        check_domain(domain)


@pytest.mark.parametrize(
    "domain", ["books.toscrape.com", "scrapethissite.com", "quotes.toscrape.com"]
)
def test_erlaubte_domains_werfen_nicht(domain):
    check_domain(domain)  # keine Ausnahme


def test_denylist_greift_in_der_modellvalidierung():
    """Nicht nur in der CLI: die Modellkonstruktion selbst muss scheitern."""
    body = _pack(domain="www.linkedin.com")
    with pytest.raises((ValidationError, DeniedTargetError)):
        SitePack.model_validate(body)


@pytest.mark.parametrize("key", ["Cookie", "Authorization", "api_key", "session_token"])
def test_zugangsdaten_schluessel_wird_abgelehnt(key):
    with pytest.raises(CredentialInPackError, match=r"fetch\.headers"):
        check_no_credentials({"fetch": {"headers": {key: "x"}}})


def test_credential_pfad_wird_genannt():
    with pytest.raises(CredentialInPackError, match=r"fetch\.headers\.Cookie"):
        check_no_credentials({"fetch": {"headers": {"Cookie": "sid=1"}}})


def test_pack_mit_credential_wird_beim_laden_abgelehnt():
    body = _pack()
    body["fetch"] = {"strategy": "http", "headers": {"Authorization": "Bearer x"}}
    with pytest.raises((ValidationError, CredentialInPackError)):
        SitePack.model_validate(body)


def test_jede_marke_ist_kommentiert():
    """Absicherung, dass die Liste nicht leer ist."""
    assert DENIED_BRANDS
    assert "amazon" in DENIED_BRANDS


def _pack(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "apiVersion": "kintsugi/v1",
        "domain": "books.toscrape.com",
        "entity": "book",
        "version": 1,
        "discovery": {"strategy": "pagination", "url_template": "p-{n}.html"},
        "extract": {"sources": [{"kind": "css", "fields": {"title": {"selector": "h1"}}}]},
        "schema": {"natural_key": ["upc"], "fields": {"upc": {"type": "string", "required": True}}},
        "compliance": {
            "tos_url": "https://x/",
            "tos_verdict": "permits",
            "tos_reviewed_at": "2026-07-21",
            "reviewed_by": "human:sven",
            "robots_checked_at": "2026-07-21",
            "public_content": True,
            "personal_data": False,
        },
    }
    base.update(overrides)
    return base
