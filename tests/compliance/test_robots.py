"""Prueft die robots.txt-Policy im Fetcher (I0.7.3, RFC 9309)."""

from __future__ import annotations

import httpx
import pytest
from kintsugi.config import Settings
from kintsugi.fetch.http import HttpFetcher
from kintsugi.fetch.robots import RobotsDenied, RobotsGate, RobotsUnavailable

CONTACT = "ops@example.invalid"


class _Site:
    """Mock-Transport: konfigurierbare robots.txt, zaehlt robots-Abrufe."""

    def __init__(self, robots_status: int = 200, robots_body: str = "", page_status: int = 200):
        self.robots_status = robots_status
        self.robots_body = robots_body
        self.page_status = page_status
        self.robots_hits = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            self.robots_hits += 1
            if self.robots_status >= 500:
                raise httpx.ConnectError("boom")
            return httpx.Response(self.robots_status, text=self.robots_body)
        return httpx.Response(self.page_status, content=b"<html>ok</html>")


def _fetcher(site: _Site) -> HttpFetcher:
    return HttpFetcher(Settings(contact=CONTACT), transport=httpx.MockTransport(site.handler))


def test_404_robots_erlaubt_die_produktseite():
    """F1: books.toscrape.com liefert 404 fuer robots.txt."""
    site = _Site(robots_status=404)
    with _fetcher(site) as f:
        result = f.fetch("https://books.toscrape.com/catalogue/a_1/index.html")
    assert result.http_status == 200


@pytest.mark.parametrize("status", [404, 410])
def test_unavailable_status_erlaubt_alles(status):
    site = _Site(robots_status=status)
    with _fetcher(site) as f:
        assert f.fetch("https://x/a").http_status == 200


@pytest.mark.parametrize("status", [401, 403])
def test_auth_status_sperrt_die_domain(status):
    site = _Site(robots_status=status)
    with _fetcher(site) as f, pytest.raises(RobotsDenied):
        f.fetch("https://x/a")


def test_5xx_bricht_mit_robots_unavailable_ab():
    site = _Site(robots_status=500)
    with _fetcher(site) as f, pytest.raises(RobotsUnavailable) as exc:
        f.fetch("https://x/a")
    assert exc.value.reason == "robots_unavailable"


def test_disallow_wird_durchgesetzt():
    site = _Site(robots_status=200, robots_body="User-agent: *\nDisallow: /geheim/\n")
    with _fetcher(site) as f:
        assert f.fetch("https://x/oeffentlich").http_status == 200
        with pytest.raises(RobotsDenied):
            f.fetch("https://x/geheim/x")


def test_robots_wird_pro_host_nur_einmal_geholt():
    site = _Site(robots_status=200, robots_body="")
    with _fetcher(site) as f:
        f.fetch("https://x/a")
        f.fetch("https://x/b")
    assert site.robots_hits == 1


def test_zweiter_host_loest_zweiten_robots_abruf_aus():
    site = _Site(robots_status=200, robots_body="")
    with _fetcher(site) as f:
        f.fetch("https://x/a")
        f.fetch("https://y/a")
    assert site.robots_hits == 2


def test_discovery_url_wird_wie_detail_url_geprueft():
    """Der Check sitzt in fetch(), also trifft er auch Paginierungsseiten."""
    site = _Site(robots_status=200, robots_body="User-agent: *\nDisallow: /catalogue/\n")
    with _fetcher(site) as f, pytest.raises(RobotsDenied):
        f.fetch("https://x/catalogue/page-2.html")


def test_crawl_delay_wird_gelesen():
    site = _Site(robots_status=200, robots_body="User-agent: *\nCrawl-delay: 3\n")
    client = httpx.Client(transport=httpx.MockTransport(site.handler))
    gate = RobotsGate(client, "kintsugi/0.1 (+ops@example.invalid)")
    assert gate.crawl_delay("https://x/a") == 3.0
    client.close()


def test_muellzeilen_und_crlf_parsen_ohne_fehler():
    body = "﻿User-agent: *\r\nDisallow: /x\r\nGarbage-Directive: 1\r\nWibble\r\n"
    site = _Site(robots_status=200, robots_body=body)
    with _fetcher(site) as f:
        assert f.fetch("https://x/erlaubt").http_status == 200


def test_compliance_md_nennt_die_robots_durchsetzung():
    from pathlib import Path

    text = Path("COMPLIANCE.md").read_text(encoding="utf-8")
    assert "kintsugi.fetch.robots" in text or "kintsugi/fetch/robots.py" in text
    assert "test_robots.py" in text
