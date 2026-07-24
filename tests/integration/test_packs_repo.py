"""Integrationstest fuer packs_repo (I0.6.11). Braucht postgres:16."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from kintsugi.packs.loader import load_pack
from kintsugi.storage.db import get_engine
from kintsugi.storage.packs_repo import activate, get_active, list_packs, upsert_pack
from kintsugi.storage.tables import site_pack
from sqlalchemy import Connection, func, select, text

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def migrated_engine():
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
def conn(migrated_engine) -> Iterator[Connection]:
    with migrated_engine.connect() as connection:
        trans = connection.begin()
        try:
            yield connection
        finally:
            trans.rollback()


def _books_pack():
    return load_pack("books.toscrape.com", "book", root=PROJECT_ROOT / "packs")


def _count(conn: Connection, domain: str, entity: str) -> int:
    return conn.execute(
        select(func.count())
        .select_from(site_pack)
        .where(site_pack.c.domain == domain, site_pack.c.entity == entity)
    ).scalar_one()


def test_upsert_erzeugt_eine_version_mit_created_by(conn):
    pack = _books_pack()
    pack_id = upsert_pack(conn, pack)
    row = conn.execute(select(site_pack).where(site_pack.c.id == pack_id)).one()
    assert row.created_by == "human:sven"
    assert row.status == "draft"
    assert row.version == 1


def test_sync_ist_idempotent(conn):
    pack = _books_pack()
    upsert_pack(conn, pack)
    upsert_pack(conn, pack)  # unveraendert
    assert _count(conn, "books.toscrape.com", "book") == 1


def test_geaenderter_pack_erzeugt_neue_version(conn):
    pack = _books_pack()
    upsert_pack(conn, pack)
    changed = pack.model_copy(update={"notes": "geaendert"})
    upsert_pack(conn, changed)
    assert _count(conn, "books.toscrape.com", "book") == 2


def test_promote_haelt_genau_eine_aktive_version(conn):
    pack = _books_pack()
    first = upsert_pack(conn, pack)
    activate(conn, first)
    second = upsert_pack(conn, pack.model_copy(update={"notes": "v2"}))
    activate(conn, second)

    active_rows = conn.execute(
        select(site_pack.c.id).where(
            site_pack.c.domain == "books.toscrape.com",
            site_pack.c.entity == "book",
            site_pack.c.status == "active",
        )
    ).all()
    assert len(active_rows) == 1
    assert active_rows[0].id == second
    assert get_active(conn, "books.toscrape.com", "book").id == second


def test_promote_reihenfolge_retire_vor_activate(conn):
    """Die umgekehrte Reihenfolge muss den partiellen Unique-Index verletzen."""
    pack = _books_pack()
    first = upsert_pack(conn, pack)
    activate(conn, first)
    second = upsert_pack(conn, pack.model_copy(update={"notes": "v2"}))

    from sqlalchemy.exc import IntegrityError

    # Falsche Reihenfolge: neue aktivieren, bevor die alte zurueckgenommen ist.
    with pytest.raises(IntegrityError):
        conn.execute(
            site_pack.update()
            .where(site_pack.c.id == second)
            .values(status="active", activated_at=func.now())
        )


def test_list_packs(conn):
    upsert_pack(conn, _books_pack())
    rows = list_packs(conn)
    assert any(r.domain == "books.toscrape.com" for r in rows)
