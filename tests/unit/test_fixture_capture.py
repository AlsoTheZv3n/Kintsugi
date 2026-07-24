"""fixtures capture: Allowlist-Gate, Fetcher-Pfad, meta.json, Index (I0.9.8a)."""

from __future__ import annotations

import gzip
import hashlib

import httpx
import pytest
from kintsugi.cli import app
from kintsugi.config import Settings
from kintsugi.fetch.http import HttpFetcher
from kintsugi.harness.fixture_model import FixtureMeta
from kintsugi.harness.fixtures_cli import build_index, capture_corpus, capture_golden, write_index
from typer.testing import CliRunner

runner = CliRunner()
CONTACT = "kintsugi-bot (+mailto:ops@example.com)"

PAGE = b"<html><head><title>Buch</title></head><body>\xc2\xa351.77 caf\xc3\xa9</body></html>"


class SpyFetcher:
    """Umhuellt einen echten HttpFetcher und zaehlt die Aufrufe (Identitaetscheck)."""

    def __init__(self, inner: HttpFetcher) -> None:
        self.inner = inner
        self.calls: list[str] = []

    def fetch(self, url, *, etag=None, last_modified=None):
        self.calls.append(url)
        return self.inner.fetch(url, etag=etag, last_modified=last_modified)


def _mock_fetcher(handler) -> HttpFetcher:
    # Kein blanker httpx.Client: der Capture-Pfad laeuft ueber HttpFetcher, der
    # den MockTransport injiziert bekommt (robots aus, damit kein robots-Abruf).
    return HttpFetcher(
        Settings(contact=CONTACT), transport=httpx.MockTransport(handler), respect_robots=False
    )


def _html_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=PAGE, headers={"content-type": "text/html"})


def test_webscraper_wird_ohne_http_abgelehnt():
    result = runner.invoke(
        app,
        [
            "fixtures",
            "capture",
            "webscraper.io",
            "--entity",
            "product",
            "--label",
            "baseline",
            "--url",
            "https://webscraper.io/x",
        ],
    )
    assert result.exit_code == 2
    assert "Disallow: /test-sites/e-commerce/" in result.output


def test_capture_geht_durch_injizierten_fetcher(tmp_path):
    spy = SpyFetcher(_mock_fetcher(_html_handler))
    url = "https://books.toscrape.com/catalogue/a_1/index.html"
    capture_golden(
        tmp_path, domain="books.toscrape.com", entity="book", label="baseline", url=url, fetcher=spy
    )
    assert spy.calls == [url]  # exakt ueber den injizierten Fetcher


def test_meta_validiert_und_content_hash_stimmt(tmp_path):
    spy = SpyFetcher(_mock_fetcher(_html_handler))
    dest = capture_golden(
        tmp_path,
        domain="books.toscrape.com",
        entity="book",
        label="baseline",
        url="https://books.toscrape.com/x",
        fetcher=spy,
    )
    meta = FixtureMeta.model_validate_json((dest / "meta.json").read_text(encoding="utf-8"))
    decompressed = gzip.decompress((dest / "page.html.gz").read_bytes())
    assert meta.content_hash == hashlib.sha256(decompressed).hexdigest()
    assert decompressed == PAGE


def test_content_type_ohne_charset_wird_woertlich_gespeichert(tmp_path):
    spy = SpyFetcher(_mock_fetcher(_html_handler))
    dest = capture_golden(
        tmp_path,
        domain="books.toscrape.com",
        entity="book",
        label="baseline",
        url="https://books.toscrape.com/x",
        fetcher=spy,
    )
    meta = FixtureMeta.model_validate_json((dest / "meta.json").read_text(encoding="utf-8"))
    assert meta.content_type == "text/html"  # kein charset erfunden


def test_synthetic_ohne_derived_from_wird_abgelehnt():
    with pytest.raises(ValueError, match="derived_from"):
        FixtureMeta(
            url="https://x/1",
            fetched_at="2026-07-24T00:00:00+00:00",
            http_status=200,
            content_type="text/html",
            content_hash="a" * 64,
            byte_size=10,
            fetcher="httpx",
            golden_label="edge:out_of_stock",
            synthetic=True,
        )


def test_alle_dateien_gz_oder_json_und_unter_512kb(tmp_path):
    spy = SpyFetcher(_mock_fetcher(_html_handler))
    capture_golden(
        tmp_path,
        domain="books.toscrape.com",
        entity="book",
        label="baseline",
        url="https://books.toscrape.com/x",
        fetcher=spy,
    )
    capture_corpus(
        tmp_path,
        domain="books.toscrape.com",
        entity="book",
        urls=["https://books.toscrape.com/catalogue/page-1.html"],
        fetcher=spy,
    )
    write_index(tmp_path)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert path.suffix in {".gz", ".json"}, path
            assert path.stat().st_size <= 512 * 1024, path


def test_index_ist_regenerierbar_und_nimmt_corpus_aus(tmp_path):
    spy = SpyFetcher(_mock_fetcher(_html_handler))
    capture_golden(
        tmp_path,
        domain="books.toscrape.com",
        entity="book",
        label="baseline",
        url="https://books.toscrape.com/x",
        fetcher=spy,
    )
    capture_corpus(
        tmp_path,
        domain="books.toscrape.com",
        entity="book",
        urls=["https://books.toscrape.com/catalogue/page-1.html"],
        fetcher=spy,
    )
    first = build_index(tmp_path)
    second = build_index(tmp_path)
    assert first == second  # deterministisch, also regenerierbar
    assert "_selftest/" in first["exempt"]
    assert "books.toscrape.com/book/corpus/" in first["exempt"]
    assert "books.toscrape.com/book/golden/baseline" in first["golden"]
