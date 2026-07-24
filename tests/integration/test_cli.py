"""kintsugi run und sources: Exit-Codes, CLI==Modulaufruf, sources (I0.9.8)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from kintsugi.cli import app
from kintsugi.config import get_settings
from kintsugi.packs.loader import load_pack
from kintsugi.storage.db import get_engine
from kintsugi.storage.tables import run as run_table
from kintsugi.storage.tables import site_pack
from sqlalchemy import Connection, func, insert, select, text
from typer.testing import CliRunner

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTACT = "kintsugi-bot (+mailto:ops@example.com)"
runner = CliRunner()


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


@pytest.fixture
def cli_env(tmp_path, monkeypatch) -> Iterator[dict[str, str]]:
    """Setzt Kontakt und einen tmp-Snapshot-Root fuer den In-Process-Aufruf."""
    monkeypatch.setenv("KINTSUGI_CONTACT", CONTACT)
    monkeypatch.setenv("KINTSUGI_SNAPSHOT_ROOT", str(tmp_path / "bronze"))
    get_settings.cache_clear()
    yield {
        **os.environ,
        "KINTSUGI_CONTACT": CONTACT,
        "KINTSUGI_SNAPSHOT_ROOT": str(tmp_path / "bronze"),
    }
    get_settings.cache_clear()


def _activate_pack(conn: Connection, base: str, *, min_rows: int = 200) -> None:
    pack = load_pack("books.toscrape.com", "book", root=Path("packs"))
    disc = pack.discovery.model_copy(
        update={
            "url_template": f"{base}/catalogue/page-{{n}}.html",
            "url_pattern": r"^http://127\.0\.0\.1:\d+/catalogue/[^/]+/index\.html$",
        }
    )
    # Hoher rps: der lokale Fixture-Server braucht keine 0.5-rps-Politeness.
    fetch = pack.fetch.model_copy(update={"rate_limit_rps": 2000.0})
    quality = pack.quality.model_copy(update={"min_rows_per_run": min_rows})
    pack = pack.model_copy(update={"discovery": disc, "fetch": fetch, "quality": quality})
    spec = json.loads(pack.model_dump_json(by_alias=True))
    conn.execute(
        insert(site_pack).values(
            domain="books.toscrape.com",
            entity="book",
            version=1,
            spec=spec,
            created_by="human:test",
            status="active",
            activated_at=func.now(),
        )
    )
    conn.commit()


def test_dry_run_exit0_mit_summenzeile(conn, books_fixture_base_url, cli_env):
    _activate_pack(conn, books_fixture_base_url)
    result = runner.invoke(app, ["run", "books.toscrape.com", "--max-urls", "5", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "rows_considered" in result.output
    assert "rows_inserted" in result.output


def test_cli_und_modulaufruf_gleiche_pack_id_und_counter(conn, books_fixture_base_url, cli_env):
    _activate_pack(conn, books_fixture_base_url, min_rows=1)  # kleiner Lauf soll ok sein
    in_proc = runner.invoke(app, ["run", "books.toscrape.com", "--max-urls", "5"])
    assert in_proc.exit_code == 0, in_proc.output

    proc = subprocess.run(
        [sys.executable, "-m", "kintsugi.cli", "run", "books.toscrape.com", "--max-urls", "5"],
        env=cli_env,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    runs = conn.execute(select(run_table.c.site_pack_id, run_table.c.metrics)).all()
    assert len(runs) == 2
    assert len({r.site_pack_id for r in runs}) == 1  # dasselbe Pack
    assert {r.metrics["counters"]["rows_considered"] for r in runs} == {5}  # namespaced (#82)


def test_unbekannte_domain_exit2(conn, cli_env):
    result = runner.invoke(app, ["run", "nosuch.example"])
    assert result.exit_code == 2
    assert "no active site pack" in result.output


def test_failed_lauf_exit1(conn, books_fixture_base_url, cli_env):
    # Echter (gekappter) Lauf unter der Schwelle -> failed -> Exit 1.
    _activate_pack(conn, books_fixture_base_url, min_rows=200)
    result = runner.invoke(app, ["run", "books.toscrape.com", "--max-urls", "5"])
    assert result.exit_code == 1, result.output


def test_sources_zeigt_books_nach_lauf(conn, books_fixture_base_url, cli_env):
    _activate_pack(conn, books_fixture_base_url)
    runner.invoke(app, ["run", "books.toscrape.com", "--max-urls", "5"])
    result = runner.invoke(app, ["sources"])
    assert result.exit_code == 0
    assert "books.toscrape.com/book" in result.output
    assert "records=" in result.output
