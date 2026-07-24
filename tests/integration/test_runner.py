"""Der synchrone Runner end to end gegen Postgres + Fixture-Server (I0.9.6)."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from alembic import command
from alembic.config import Config
from kintsugi.config import Settings
from kintsugi.fetch.base import FetchOutcome, FetchResult
from kintsugi.fetch.http import HttpFetcher
from kintsugi.fetch.ratelimit import DomainLimiter
from kintsugi.packs.loader import load_pack
from kintsugi.runner import NoActivePackError, run
from kintsugi.storage.db import get_engine
from kintsugi.storage.tables import record, site_pack, snapshot
from kintsugi.storage.tables import run as run_table
from sqlalchemy import Connection, func, insert, select, text

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTACT = "kintsugi-bot (+mailto:ops@example.com)"
CONSENT_BODY = (
    b'<html><body><div id="onetrust-consent-sdk">We value your privacy</div>'
    b"<p>Bitte stimmen Sie zu.</p></body></html>"
)


@pytest.fixture(scope="module")
def engine():
    eng = get_engine()
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"kein postgres erreichbar: {exc}")
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    return eng


@pytest.fixture
def conn(engine):
    with engine.connect() as connection:
        connection.execute(text("TRUNCATE site_pack CASCADE"))
        connection.commit()
        yield connection


def _settings(tmp_path) -> Settings:
    return Settings(contact=CONTACT, snapshot_root=tmp_path / "bronze")


def _fast_fetcher() -> HttpFetcher:
    return HttpFetcher(
        Settings(contact=CONTACT), limiter=DomainLimiter(2000.0, 4), respect_robots=True
    )


def _activate_pack(conn: Connection, base: str) -> UUID:
    pack = load_pack("books.toscrape.com", "book", root=Path("packs"))
    disc = pack.discovery.model_copy(
        update={
            "url_template": f"{base}/catalogue/page-{{n}}.html",
            "url_pattern": r"^http://127\.0\.0\.1:\d+/catalogue/[^/]+/index\.html$",
        }
    )
    pack = pack.model_copy(update={"discovery": disc})
    spec = json.loads(pack.model_dump_json(by_alias=True))
    pack_id = conn.execute(
        insert(site_pack)
        .values(
            domain="books.toscrape.com",
            entity="book",
            version=1,
            spec=spec,
            created_by="human:test",
            status="active",
            activated_at=func.now(),
        )
        .returning(site_pack.c.id)
    ).scalar_one()
    conn.commit()
    return pack_id


def _count(conn: Connection, table) -> int:
    return conn.execute(select(func.count()).select_from(table)).scalar_one()


class WrapFetcher:
    """Delegiert, faelscht aber das Ergebnis fuer Detailseiten nach ``mode``."""

    def __init__(self, inner: HttpFetcher, *, mode: str) -> None:
        self.inner = inner
        self.mode = mode

    def _is_detail(self, url: str) -> bool:
        return url.endswith("/index.html") and "page-" not in url

    def fetch(self, url, *, etag=None, last_modified=None):
        if self._is_detail(url) and self.mode == "block":
            return FetchResult(
                url=url,
                final_url=url,
                http_status=200,
                headers={},
                body=CONSENT_BODY,
                content_type="text/html",
                encoding="utf-8",
                elapsed_ms=1,
                fetcher="httpx",
                from_cache=False,
                outcome=FetchOutcome.ok,
            )
        if (
            self._is_detail(url)
            and self.mode == "fail_one"
            and url.endswith("book-005_5/index.html")
        ):
            raise httpx.ConnectError("injizierter Netzfehler")
        return self.inner.fetch(url, etag=etag, last_modified=last_modified)


def test_voller_lauf_schreibt_mindestens_200_records(conn, books_fixture_base_url, tmp_path):
    _activate_pack(conn, books_fixture_base_url)
    result = run("books.toscrape.com", fetcher=_fast_fetcher(), settings=_settings(tmp_path))
    assert result.status == "ok"
    current = conn.execute(
        select(func.count()).select_from(record).where(record.c.valid_to.is_(None))
    ).scalar_one()
    assert current >= 200
    finished = conn.execute(select(run_table.c.finished_at)).scalar_one()
    assert finished is not None


def test_runner_ist_synchron():
    source = (PROJECT_ROOT / "kintsugi" / "runner.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.AsyncFunctionDef, ast.Await)), node
        if isinstance(node, ast.Import):
            assert all(alias.name != "asyncio" for alias in node.names)
    assert "asyncpg" not in source


def test_unbekannte_domain_wirft_no_active_pack(conn, tmp_path):
    with pytest.raises(NoActivePackError):
        run("nosuch.example", settings=_settings(tmp_path))


def test_unter_schwelle_ohne_ausfall_ist_failed(conn, books_fixture_base_url, tmp_path):
    _activate_pack(conn, books_fixture_base_url)
    result = run(
        "books.toscrape.com", fetcher=_fast_fetcher(), settings=_settings(tmp_path), max_urls=5
    )
    assert result.status == "failed"
    err = conn.execute(select(run_table.c.error)).scalar_one()
    assert err is not None


def test_teilausfall_ist_degraded_auch_unter_schwelle(conn, books_fixture_base_url, tmp_path):
    _activate_pack(conn, books_fixture_base_url)
    fetcher = WrapFetcher(_fast_fetcher(), mode="fail_one")
    result = run("books.toscrape.com", fetcher=fetcher, settings=_settings(tmp_path), max_urls=10)
    assert result.status == "degraded"


def test_consent_wall_bricht_ab_und_wird_nicht_extrahiert(conn, books_fixture_base_url, tmp_path):
    _activate_pack(conn, books_fixture_base_url)
    fetcher = WrapFetcher(_fast_fetcher(), mode="block")
    result = run("books.toscrape.com", fetcher=fetcher, settings=_settings(tmp_path))
    # README/#67: eine Consent-Wall bricht die Domain ab (failed), nie extrahiert.
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.startswith("blocked:")
    assert _count(conn, record) == 0  # nichts extrahiert
    assert _count(conn, snapshot) >= 1  # aber der Block-Snapshot ist da


def test_dry_run_schreibt_snapshots_aber_keine_records(conn, books_fixture_base_url, tmp_path):
    _activate_pack(conn, books_fixture_base_url)
    result = run(
        "books.toscrape.com",
        fetcher=_fast_fetcher(),
        settings=_settings(tmp_path),
        max_urls=10,
        dry_run=True,
    )
    assert result.status in {"ok", "degraded", "failed"}  # Status egal
    assert _count(conn, record) == 0  # dry_run: keine records
    assert _count(conn, snapshot) > 0  # aber Snapshots wurden geschrieben
