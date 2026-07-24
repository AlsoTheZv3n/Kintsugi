"""Offline books-Corpus und Fixture-Server (I0.9.9)."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import pytest
from selectolax.lexbor import LexborHTMLParser

BOOK = Path(__file__).resolve().parents[2] / "fixtures" / "books.toscrape.com" / "book"
CORPUS = BOOK / "corpus"
LINK_SELECTOR = "article.product_pod h3 a"


def _manifest() -> dict[str, dict[str, object]]:
    return json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))


def _decompress(path: str) -> bytes:
    import gzip

    entry = _manifest()[path]
    return gzip.decompress((CORPUS / entry["blob"]).read_bytes())


def _links_on(index_path: str, base: str) -> list[str]:
    html = _decompress(index_path).decode("utf-8")
    tree = LexborHTMLParser(html)
    out = []
    for node in tree.css(LINK_SELECTOR):
        href = node.attributes.get("href")
        if href:
            out.append(urljoin(urljoin(base, index_path), href))
    return out


@pytest.mark.parametrize("path", ["/robots.txt", "/sitemap.xml", "/catalogue/page-13.html"])
def test_fehlende_pfade_liefern_404(books_fixture_base_url, path):
    resp = httpx.get(books_fixture_base_url + path)
    assert resp.status_code == 404


def test_manifest_hat_index_und_detailseiten():
    manifest = _manifest()
    for n in range(1, 13):
        assert f"/catalogue/page-{n}.html" in manifest
    details = [p for p in manifest if p.endswith("/index.html")]
    assert len(details) >= 240
    links = _links_on("/catalogue/page-1.html", "http://x")
    assert len(links) == 20


def test_walk_erreicht_mindestens_240_detailseiten():
    manifest = _manifest()
    seen: set[str] = set()
    for n in range(1, 13):
        for url in _links_on(f"/catalogue/page-{n}.html", "http://x"):
            seen.add(urlsplit(url).path)
    assert len(seen) >= 240
    # Keine der gewalkten URLs ist eine synthetische oder Edge-Fixture.
    assert all("edge" not in path for path in seen)
    # und jede gewalkte URL steht im Corpus-Manifest.
    assert seen <= set(manifest)


def test_golden_kanten_nicht_ascii_und_out_of_stock():
    metas = {}
    for d in (BOOK / "golden").iterdir():
        if not d.is_dir():
            continue
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        # baseline traegt das aeltere CssExtractor-Format (Key 'label'); die
        # FixtureMeta-Kanten tragen 'golden_label'.
        if "golden_label" in meta:
            metas[meta["golden_label"]] = d
    # Mindestens ein Golden-Titel traegt ein Nicht-ASCII-Zeichen.
    import gzip

    titles = []
    for d in metas.values():
        html = gzip.decompress((d / "page.html.gz").read_bytes()).decode("utf-8")
        titles.append(html)
    assert any(any(ord(ch) > 127 for ch in html) for html in titles)

    oos_dir = metas["edge:out_of_stock"]
    oos_meta = json.loads((oos_dir / "meta.json").read_text(encoding="utf-8"))
    oos_html = gzip.decompress((oos_dir / "page.html.gz").read_bytes()).decode("utf-8")
    assert "Out of stock" in oos_html
    assert oos_meta["synthetic"] is True
    assert oos_meta["derived_from"]
    assert oos_meta["derived_from"].strip()


def test_conditional_request_und_content_type(books_fixture_base_url):
    url = books_fixture_base_url + "/catalogue/page-1.html"
    first = httpx.get(url)
    assert first.status_code == 200
    assert first.headers["content-type"] == "text/html"  # exakt, kein charset
    etag = first.headers["etag"]

    second = httpx.get(url, headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.content == b""


def test_dateibudget_und_endungen():
    total = 0
    for path in (BOOK.parents[1]).rglob("*"):
        if path.is_file() and "books.toscrape.com" in path.parts:
            assert path.suffix in {".gz", ".json"}, path
            size = path.stat().st_size
            assert size <= 512 * 1024, path
            total += size
    assert total <= 5 * 1024 * 1024


def test_server_bindet_loopback(books_fixture_base_url):
    host = urlsplit(books_fixture_base_url).hostname
    assert host == "127.0.0.1"
