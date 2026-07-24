"""fixtures import: is_golden-Snapshots, Idempotenz, PII-Gate (I1.3.2)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from kintsugi.cli import app
from kintsugi.config import get_settings
from kintsugi.harness.fixtures_cli import import_golden
from kintsugi.packs.loader import load_pack
from kintsugi.storage.db import get_engine
from kintsugi.storage.snapshots import FilesystemSnapshotStore
from kintsugi.storage.tables import site_pack, snapshot
from sqlalchemy import Connection, func, insert, select, text
from typer.testing import CliRunner

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT_ROOT / "fixtures"
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


def _activate(conn: Connection, *, personal_data: bool = False) -> UUID:
    pack = load_pack("books.toscrape.com", "book", root=Path("packs"))
    if personal_data:
        comp = pack.compliance.model_copy(
            update={"personal_data": True, "legal_basis": "berechtigtes Interesse (Test)"}
        )
        pack = pack.model_copy(update={"compliance": comp})
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


@pytest.fixture
def cli_env(tmp_path, monkeypatch) -> Iterator[dict[str, str]]:
    monkeypatch.setenv("KINTSUGI_CONTACT", CONTACT)
    monkeypatch.setenv("KINTSUGI_SNAPSHOT_ROOT", str(tmp_path / "bronze"))
    get_settings.cache_clear()
    yield {
        **os.environ,
        "KINTSUGI_CONTACT": CONTACT,
        "KINTSUGI_SNAPSHOT_ROOT": str(tmp_path / "bronze"),
    }
    get_settings.cache_clear()


def _golden_count(conn: Connection) -> int:
    return conn.execute(
        select(func.count()).select_from(snapshot).where(snapshot.c.is_golden.is_(True))
    ).scalar_one()


def test_import_ist_idempotent_und_labelt(conn, tmp_path):
    pack_id = _activate(conn)
    store = FilesystemSnapshotStore(tmp_path / "bronze")

    first = import_golden(
        conn,
        site_pack_id=pack_id,
        domain="books.toscrape.com",
        entity="book",
        root=FIXTURES,
        store=store,
    )
    assert first >= 1
    count_after_first = _golden_count(conn)
    assert count_after_first == first

    labels = (
        conn.execute(select(snapshot.c.golden_label).where(snapshot.c.is_golden.is_(True)))
        .scalars()
        .all()
    )
    assert all(label is not None for label in labels)
    assert any(label.startswith("edge:") for label in labels)

    second = import_golden(
        conn,
        site_pack_id=pack_id,
        domain="books.toscrape.com",
        entity="book",
        root=FIXTURES,
        store=store,
    )
    assert second == 0
    assert _golden_count(conn) == count_after_first  # unveraendert


def test_personal_data_gate_exit3(conn, cli_env):
    _activate(conn, personal_data=True)
    result = runner.invoke(app, ["fixtures", "import", "books.toscrape.com", "--entity", "book"])
    assert result.exit_code == 3, result.output
    assert _golden_count(conn) == 0  # nichts eingespielt


def test_nur_synchroner_treiber():
    # Token zusammengesetzt, damit er nicht woertlich in dieser Datei steht.
    token = "async" + "pg"
    module = (PROJECT_ROOT / "kintsugi" / "harness" / "fixtures_cli.py").read_text("utf-8")
    this = Path(__file__).read_text("utf-8")
    assert token not in module
    assert token not in this
