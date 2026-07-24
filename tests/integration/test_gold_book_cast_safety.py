"""gold_book ueberlebt eine feindliche Zeile und schliesst Violations aus (I0.8.3)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from kintsugi.storage.db import get_engine
from sqlalchemy import Connection, text

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


def _parents(conn: Connection) -> tuple[uuid.UUID, uuid.UUID]:
    sp = conn.execute(
        text(
            "INSERT INTO site_pack (domain, entity, version, spec, created_by) "
            "VALUES ('books.toscrape.com', 'book', 1, '{\"domain\": \"books.toscrape.com\"}', "
            "'human:sven') RETURNING id"
        )
    ).scalar_one()
    run = conn.execute(
        text("INSERT INTO run (site_pack_id, trigger) VALUES (:sp, 'manual') RETURNING id"),
        {"sp": sp},
    ).scalar_one()
    snap = conn.execute(
        text(
            "INSERT INTO snapshot (run_id, url, http_status, content_hash, byte_size, blob_key, "
            "fetcher) VALUES (:r, 'https://x/1', 200, :h, 10, 'raw/x', 'httpx') RETURNING id"
        ),
        {"r": run, "h": b"\x00" * 32},
    ).scalar_one()
    return sp, snap


def _insert_record(conn, sp, snap, *, upc, payload, quality="{}") -> None:
    conn.execute(
        text(
            "INSERT INTO record (entity, natural_key, snapshot_id, site_pack_id, payload, "
            "payload_hash, quality) VALUES ('book', :nk, :s, :sp, CAST(:p AS jsonb), :h, "
            "CAST(:q AS jsonb))"
        ),
        {"nk": upc, "s": snap, "sp": sp, "p": payload, "h": b"\x00" * 32, "q": quality},
    )


def test_feindliche_zeile_bricht_gold_book_nicht():
    """Ein Payload mit price='n/a' darf keinen Full-Table-Fehler ausloesen."""
    eng = get_engine()
    with eng.begin() as conn:
        sp, snap = _parents(conn)
        _insert_record(conn, sp, snap, upc="upc-hostile", payload='{"title": "T", "price": "n/a"}')
        count = conn.execute(text("SELECT count(*) FROM gold_book")).scalar_one()
        conn.execute(text("DELETE FROM record"))
        conn.execute(text("DELETE FROM snapshot"))
        conn.execute(text("DELETE FROM run"))
        conn.execute(text("DELETE FROM site_pack"))
    assert count >= 0  # der SELECT ist ueberhaupt durchgelaufen


def test_range_violation_wird_aus_gold_book_ausgeschlossen(conn):
    sp, snap = _parents(conn)
    _insert_record(
        conn,
        sp,
        snap,
        upc="upc-badrange",
        payload='{"title": "T", "price": "99999", "currency": "GBP"}',
        quality='{"violations": ["range_violation:price"]}',
    )
    rows = conn.execute(
        text("SELECT count(*) FROM gold_book WHERE upc = 'upc-badrange'")
    ).scalar_one()
    assert rows == 0


def test_saubere_zeile_erscheint_in_gold_book(conn):
    sp, snap = _parents(conn)
    _insert_record(
        conn,
        sp,
        snap,
        upc="upc-clean",
        payload='{"title": "T", "price": "51.77", "currency": "GBP", "availability": "22"}',
    )
    row = conn.execute(
        text("SELECT price, availability FROM gold_book WHERE upc = 'upc-clean'")
    ).one()
    assert row.price == Decimal("51.77")  # ::numeric -> Decimal, nicht float
    assert row.availability == 22
