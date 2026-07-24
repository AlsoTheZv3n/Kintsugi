"""run.metrics: considered-vs-written, Zweitlauf-Idempotenz, 200-Grenze (I0.9.7)."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from kintsugi.config import Settings
from kintsugi.fetch.http import HttpFetcher
from kintsugi.fetch.ratelimit import DomainLimiter
from kintsugi.packs.loader import load_pack
from kintsugi.runner import run
from kintsugi.storage.db import get_engine
from kintsugi.storage.tables import incident, record, site_pack
from kintsugi.storage.tables import run as run_table
from sqlalchemy import Connection, func, insert, select, text

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTACT = "kintsugi-bot (+mailto:ops@example.com)"


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


def _activate_pack(conn: Connection, base: str, *, min_rows: int = 200) -> UUID:
    pack = load_pack("books.toscrape.com", "book", root=Path("packs"))
    disc = pack.discovery.model_copy(
        update={
            "url_template": f"{base}/catalogue/page-{{n}}.html",
            "url_pattern": r"^http://127\.0\.0\.1:\d+/catalogue/[^/]+/index\.html$",
        }
    )
    quality = pack.quality.model_copy(update={"min_rows_per_run": min_rows})
    pack = pack.model_copy(update={"discovery": disc, "quality": quality})
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


def _metrics(conn: Connection, run_id: UUID) -> dict:
    return conn.execute(select(run_table.c.metrics).where(run_table.c.id == run_id)).scalar_one()


def test_erster_lauf_schreibt_und_zweiter_ist_unchanged(conn, books_fixture_base_url, tmp_path):
    _activate_pack(conn, books_fixture_base_url)
    settings = _settings(tmp_path)

    r1 = run("books.toscrape.com", fetcher=_fast_fetcher(), settings=settings)
    m1 = _metrics(conn, r1.run_id)
    record_count = conn.execute(select(func.count()).select_from(record)).scalar_one()
    assert m1["rows_inserted"] == m1["rows_valid"] == record_count

    r2 = run("books.toscrape.com", fetcher=_fast_fetcher(), settings=settings)
    m2 = _metrics(conn, r2.run_id)
    assert r2.status == "ok"
    assert m2["rows_inserted"] == 0
    assert m2["rows_unchanged"] == m2["rows_considered"] >= 200
    # Kein falscher row_count_anomaly-Incident, obwohl null geschrieben wurde.
    assert conn.execute(select(func.count()).select_from(incident)).scalar_one() == 0


def test_pages_fetched_spiegelt_spalte_und_http_summe(conn, books_fixture_base_url, tmp_path):
    _activate_pack(conn, books_fixture_base_url)
    r = run("books.toscrape.com", fetcher=_fast_fetcher(), settings=_settings(tmp_path))
    row = conn.execute(
        select(run_table.c.pages_fetched, run_table.c.rows_extracted, run_table.c.metrics).where(
            run_table.c.id == r.run_id
        )
    ).one()
    assert row.pages_fetched == row.metrics["pages_fetched"]
    assert row.rows_extracted == row.metrics["rows_extracted"]
    assert sum(row.metrics["http"].values()) == row.metrics["pages_fetched"]
    # rows_valid + alle Ablehnungen == extrahierte Zeilen.
    rejected = sum(row.metrics["rows_rejected"].values())
    assert row.metrics["rows_valid"] + rejected == row.metrics["rows_extracted"]


def test_200_betrachtete_zeilen_sind_ok_199_ist_failed(conn, books_fixture_base_url, tmp_path):
    _activate_pack(conn, books_fixture_base_url, min_rows=200)
    ok = run(
        "books.toscrape.com", fetcher=_fast_fetcher(), settings=_settings(tmp_path), max_urls=200
    )
    assert ok.status == "ok"
    assert ok.counters.rows_valid + ok.counters.rows_unchanged == 200

    conn.execute(text("TRUNCATE record CASCADE"))
    conn.commit()
    below = run(
        "books.toscrape.com", fetcher=_fast_fetcher(), settings=_settings(tmp_path), max_urls=199
    )
    assert below.status == "failed"
