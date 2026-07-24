"""Profil in run.metrics: Round-Trip, strikte Lesung, iter_metrics, eine Tx (I1.1.4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import kintsugi.runner as runner_mod
import pytest
from alembic import command
from alembic.config import Config
from kintsugi.config import Settings
from kintsugi.fetch.http import HttpFetcher
from kintsugi.fetch.ratelimit import DomainLimiter
from kintsugi.packs.loader import load_pack
from kintsugi.runner import load_profile, run
from kintsugi.storage.db import get_engine
from kintsugi.storage.tables import record, site_pack
from kintsugi.storage.tables import run as run_table
from pydantic import ValidationError
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


def _fast() -> HttpFetcher:
    return HttpFetcher(
        Settings(contact=CONTACT), limiter=DomainLimiter(2000.0, 4), respect_robots=True
    )


def _activate(conn: Connection, base: str) -> UUID:
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


def _seed_prior_runs(conn: Connection, sp: UUID, n: int, rows: int) -> None:
    for i in range(n):
        started = datetime.now(UTC) - timedelta(days=i + 1)
        conn.execute(
            insert(run_table).values(
                site_pack_id=sp,
                trigger="manual",
                status="ok",
                started_at=started,
                finished_at=started,
                rows_extracted=rows,
                metrics={"counters": {}, "quality": {"fill_rate": {"title": 1.0}}},
            )
        )
    conn.commit()


def test_gespeicherte_metriken_round_trip(conn, books_fixture_base_url, tmp_path):
    _activate(conn, books_fixture_base_url)
    result = run("books.toscrape.com", fetcher=_fast(), settings=_settings(tmp_path))
    assert result.profile is not None
    with get_engine().connect() as fresh:
        loaded = load_profile(fresh, result.run_id)
    assert loaded.model_dump(mode="json") == result.profile.model_dump(mode="json")


def test_load_profile_wirft_bei_schema_drift(conn, books_fixture_base_url, tmp_path):
    _activate(conn, books_fixture_base_url)
    result = run("books.toscrape.com", fetcher=_fast(), settings=_settings(tmp_path))
    # Einen Pflichtschluessel aus dem gespeicherten Qualitaetsblock entfernen.
    conn.execute(
        run_table.update()
        .where(run_table.c.id == result.run_id)
        .values(metrics=text("metrics #- '{quality,fill_rate}'"))
    )
    conn.commit()
    with get_engine().connect() as fresh, pytest.raises(ValidationError):
        load_profile(fresh, result.run_id)


def test_iter_metrics_namen_und_zusatzmetriken(conn, books_fixture_base_url, tmp_path):
    _activate(conn, books_fixture_base_url)
    result = run("books.toscrape.com", fetcher=_fast(), settings=_settings(tmp_path))
    assert result.profile is not None
    names = {
        name
        for name, _labels, _value in result.profile.iter_metrics(
            "books.toscrape.com", "book", duration_seconds=1.0, status=result.status
        )
    }
    assert "kintsugi_field_fill_rate" in names
    assert "kintsugi_rows_extracted_total" in names
    assert "kintsugi_run_duration_seconds" in names
    assert "kintsugi_pages_fetched_total" in names


def test_row_count_deviation_haengt_am_baseline(conn, books_fixture_base_url, tmp_path):
    sp = _activate(conn, books_fixture_base_url)
    # Lauf 1: keine Historie -> insufficient -> deviation nicht emittiert.
    r1 = run("books.toscrape.com", fetcher=_fast(), settings=_settings(tmp_path))
    assert r1.profile is not None
    names1 = {n for n, _l, _v in r1.profile.iter_metrics("books.toscrape.com", "book")}
    assert "kintsugi_row_count_deviation" not in names1

    # Genug Historie seeden -> Median vorhanden -> deviation emittiert.
    _seed_prior_runs(conn, sp, n=3, rows=240)
    r2 = run("books.toscrape.com", fetcher=_fast(), settings=_settings(tmp_path))
    assert r2.profile is not None
    names2 = {n for n, _l, _v in r2.profile.iter_metrics("books.toscrape.com", "book")}
    assert "kintsugi_row_count_deviation" in names2


def test_lauf_gegen_fixture_server_ist_sauber(conn, books_fixture_base_url, tmp_path):
    _activate(conn, books_fixture_base_url)
    result = run("books.toscrape.com", fetcher=_fast(), settings=_settings(tmp_path))
    assert result.profile is not None
    assert result.profile.rows_written >= 200
    # Keine 404 unter den Detailseiten: das einzige 404 ist der Paginierungs-
    # Terminator (page-13 / live page-51), den #77 bewusst in pages_fetched zaehlt.
    assert result.profile.http.get("404", 0) <= 1
    assert result.profile.http["200"] >= 240


def test_records_und_metrics_in_einer_transaktion(
    conn, books_fixture_base_url, tmp_path, monkeypatch
):
    _activate(conn, books_fixture_base_url)

    def _boom(*_a, **_k):
        raise RuntimeError("Fehler nach den Record-Writes")

    monkeypatch.setattr(runner_mod, "compute_profile", _boom)
    with pytest.raises(RuntimeError):
        run("books.toscrape.com", fetcher=_fast(), settings=_settings(tmp_path), max_urls=10)

    with get_engine().connect() as fresh:
        assert fresh.execute(select(func.count()).select_from(record)).scalar_one() == 0
        metrics = fresh.execute(select(run_table.c.metrics)).scalar_one()
    assert metrics == {}  # eine Transaktion: nichts committet
