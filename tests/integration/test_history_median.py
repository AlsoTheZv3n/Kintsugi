"""load_history: 14-Tage-Median, Baseline-Wachhund, Scope (domain, entity) (I1.1.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from kintsugi.quality.history import MIN_QUALIFYING_RUNS, load_history
from kintsugi.storage.db import get_engine
from kintsugi.storage.tables import run as run_table
from kintsugi.storage.tables import site_pack
from sqlalchemy import Connection, func, insert, select, text

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


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


def _pack(conn: Connection, version: int = 1) -> UUID:
    return conn.execute(
        insert(site_pack)
        .values(
            domain="books.toscrape.com",
            entity="book",
            version=version,
            spec={"domain": "books.toscrape.com"},
            created_by="human:test",
        )
        .returning(site_pack.c.id)
    ).scalar_one()


def _run(
    conn: Connection,
    sp: UUID,
    *,
    days_ago: float = 1,
    status: str = "ok",
    trigger: str = "manual",
    rows: int = 100,
    fill_rate: dict[str, float] | None = None,
) -> None:
    started = NOW - timedelta(days=days_ago)
    metrics: dict = {"counters": {}}
    if fill_rate is not None:
        metrics["quality"] = {"fill_rate": fill_rate}
    conn.execute(
        insert(run_table).values(
            site_pack_id=sp,
            trigger=trigger,
            status=status,
            started_at=started,
            finished_at=started,
            rows_extracted=rows,
            metrics=metrics,
        )
    )


def test_unter_drei_laeufen_ist_baseline_unzureichend(conn):
    sp = _pack(conn)
    _run(conn, sp, rows=100)
    _run(conn, sp, rows=110)
    conn.commit()
    hist = load_history(conn, "books.toscrape.com", "book", NOW)
    assert hist.insufficient_baseline is True
    assert hist.median_14d is None

    _run(conn, sp, rows=120, fill_rate={"title": 1.0})
    conn.commit()
    hist3 = load_history(conn, "books.toscrape.com", "book", NOW)
    assert hist3.insufficient_baseline is False
    assert hist3.median_14d == 110  # median(100, 110, 120)


def test_median_ueber_alle_pack_versionen(conn):
    sp1 = _pack(conn, 1)
    sp2 = _pack(conn, 2)
    sp3 = _pack(conn, 3)
    _run(conn, sp1, rows=100)
    _run(conn, sp2, rows=200)
    _run(conn, sp3, rows=300)
    conn.commit()
    hist = load_history(conn, "books.toscrape.com", "book", NOW)
    assert hist.qualifying_runs == 3
    assert hist.median_14d == 200  # deckt alle drei Versionen

    # site_pack_id-scoped auf die neueste Version allein waere unzureichend.
    newest_only = conn.execute(
        select(func.count()).select_from(run_table).where(run_table.c.site_pack_id == sp3)
    ).scalar_one()
    assert newest_only < MIN_QUALIFYING_RUNS


def test_canary_replay_failed_zaehlen_nicht(conn):
    sp = _pack(conn)
    _run(conn, sp, rows=100)
    _run(conn, sp, rows=100)
    _run(conn, sp, rows=100)
    _run(conn, sp, rows=9999, trigger="canary")
    _run(conn, sp, rows=9999, trigger="replay")
    _run(conn, sp, rows=9999, status="failed")
    conn.commit()
    hist = load_history(conn, "books.toscrape.com", "book", NOW)
    assert hist.qualifying_runs == 3
    assert hist.median_14d == 100  # die 9999er zaehlen nicht mit


def test_lauf_ausserhalb_des_fensters_ist_ausgeschlossen(conn):
    sp = _pack(conn)
    _run(conn, sp, rows=100)
    _run(conn, sp, rows=100)
    _run(conn, sp, rows=100)
    _run(conn, sp, days_ago=15, rows=9999)  # ausserhalb 14 Tage
    conn.commit()
    hist = load_history(conn, "books.toscrape.com", "book", NOW)
    assert hist.qualifying_runs == 3
    assert hist.median_14d == 100


def test_fill_rate_median_je_feld(conn):
    sp = _pack(conn)
    _run(conn, sp, fill_rate={"title": 0.8})
    _run(conn, sp, fill_rate={"title": 0.9})
    _run(conn, sp, fill_rate={"title": 1.0})
    conn.commit()
    hist = load_history(conn, "books.toscrape.com", "book", NOW)
    assert hist.fill_rate_median["title"] == 0.9
