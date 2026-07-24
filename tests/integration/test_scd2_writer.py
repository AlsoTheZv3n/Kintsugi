"""SCD-2-Writer: Historie ohne Luecken, Duplikatregel, record_current (I0.9.3)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import kintsugi.storage.records as records_mod
import pytest
from alembic import command
from alembic.config import Config
from kintsugi.storage.db import get_engine
from kintsugi.storage.records import RecordRow, write_records
from kintsugi.storage.tables import record, run, site_pack, snapshot
from sqlalchemy import Connection, func, insert, select, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOMAIN = "books.toscrape.com"
T1 = datetime(2026, 7, 20, tzinfo=UTC)
T2 = datetime(2026, 7, 21, tzinfo=UTC)


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


def _seed_pack(conn: Connection) -> UUID:
    return conn.execute(
        insert(site_pack)
        .values(
            domain=DOMAIN,
            entity="book",
            version=1,
            spec={"domain": DOMAIN},
            created_by="human:sven",
        )
        .returning(site_pack.c.id)
    ).scalar_one()


def _seed_run(conn: Connection, sp: UUID) -> UUID:
    return conn.execute(
        insert(run).values(site_pack_id=sp, trigger="manual").returning(run.c.id)
    ).scalar_one()


def _seed_snapshot(conn: Connection, run_id: UUID) -> UUID:
    return conn.execute(
        insert(snapshot)
        .values(
            run_id=run_id,
            url="https://x/1",
            http_status=200,
            content_hash=b"\x00" * 32,
            byte_size=1,
            blob_key="raw/x",
            fetcher="httpx",
        )
        .returning(snapshot.c.id)
    ).scalar_one()


def _current(conn: Connection, nk: str):
    return conn.execute(
        select(
            record.c.id,
            record.c.payload,
            record.c.valid_from,
            record.c.last_seen_at,
            record.c.last_seen_run_id,
        ).where(record.c.natural_key == nk, record.c.valid_to.is_(None))
    ).one()


def _count_rows(conn: Connection, nk: str, *, current_only: bool = False) -> int:
    stmt = select(func.count()).select_from(record).where(record.c.natural_key == nk)
    if current_only:
        stmt = stmt.where(record.c.valid_to.is_(None))
    return conn.execute(stmt).scalar_one()


def test_identischer_payload_schreibt_keine_zweite_zeile(conn):
    sp = _seed_pack(conn)
    run1 = _seed_run(conn, sp)
    snap1 = _seed_snapshot(conn, run1)
    conn.commit()
    c1 = write_records(
        conn,
        entity="book",
        rows=[RecordRow("k", {"a": 1}, snap1)],
        site_pack_id=sp,
        run_id=run1,
        valid_from=T1,
    )
    conn.commit()
    assert c1.inserted == 1
    id1 = _current(conn, "k").id

    run2 = _seed_run(conn, sp)
    snap2 = _seed_snapshot(conn, run2)
    conn.commit()
    c2 = write_records(
        conn,
        entity="book",
        rows=[RecordRow("k", {"a": 1}, snap2)],
        site_pack_id=sp,
        run_id=run2,
        valid_from=T2,
    )
    conn.commit()
    assert c2.unchanged == 1
    assert _count_rows(conn, "k") == 1
    row = _current(conn, "k")
    assert row.id == id1
    assert row.last_seen_run_id == run2
    assert row.last_seen_at == T2


def test_geaenderter_payload_versioniert_ohne_luecke(conn):
    sp = _seed_pack(conn)
    run1 = _seed_run(conn, sp)
    snap1 = _seed_snapshot(conn, run1)
    conn.commit()
    write_records(
        conn,
        entity="book",
        rows=[RecordRow("k", {"a": 1}, snap1)],
        site_pack_id=sp,
        run_id=run1,
        valid_from=T1,
    )
    conn.commit()

    run2 = _seed_run(conn, sp)
    snap2 = _seed_snapshot(conn, run2)
    conn.commit()
    c2 = write_records(
        conn,
        entity="book",
        rows=[RecordRow("k", {"a": 2}, snap2)],
        site_pack_id=sp,
        run_id=run2,
        valid_from=T2,
    )
    conn.commit()
    assert c2.versioned == 1

    old_valid_to = conn.execute(
        select(record.c.valid_to).where(record.c.natural_key == "k", record.c.valid_to.isnot(None))
    ).scalar_one()
    new_valid_from = conn.execute(
        select(record.c.valid_from).where(record.c.natural_key == "k", record.c.valid_to.is_(None))
    ).scalar_one()
    assert old_valid_to == new_valid_from == T2
    assert _count_rows(conn, "k", current_only=True) == 1


@pytest.mark.parametrize(
    ("order", "survivor"),
    [(["first", "second"], "first"), (["second", "first"], "second")],
)
def test_duplikat_erstes_vorkommen_gewinnt(conn, order, survivor):
    sp = _seed_pack(conn)
    run1 = _seed_run(conn, sp)
    snap = _seed_snapshot(conn, run1)
    conn.commit()
    rows = [RecordRow("k", {"v": v}, snap) for v in order]
    c = write_records(conn, entity="book", rows=rows, site_pack_id=sp, run_id=run1, valid_from=T1)
    conn.commit()
    assert c.inserted == 1
    assert c.duplicates == 1
    assert _current(conn, "k").payload == {"v": survivor}


def test_summe_der_zaehler_gleich_eingabezeilen(conn):
    sp = _seed_pack(conn)
    run1 = _seed_run(conn, sp)
    snap = _seed_snapshot(conn, run1)
    conn.commit()
    rows = [
        RecordRow("k1", {"a": 1}, snap),
        RecordRow("k2", {"a": 1}, snap),
        RecordRow("k1", {"a": 9}, snap),  # Duplikat von k1
    ]
    c = write_records(conn, entity="book", rows=rows, site_pack_id=sp, run_id=run1, valid_from=T1)
    assert c.inserted == 2
    assert c.duplicates == 1
    assert c.total == len(rows)


def test_zweite_aktuelle_zeile_wirft_integrityerror(conn, monkeypatch):
    sp = _seed_pack(conn)
    run1 = _seed_run(conn, sp)
    snap = _seed_snapshot(conn, run1)
    conn.commit()
    write_records(
        conn,
        entity="book",
        rows=[RecordRow("k", {"a": 1}, snap)],
        site_pack_id=sp,
        run_id=run1,
        valid_from=T1,
    )
    conn.commit()

    # Simuliert den Fall, den record_current verhindert: der Writer "sieht" die
    # aktuelle Zeile nicht und versucht eine zweite einzufuegen. Der Index muss
    # das ablehnen, und der Writer darf den Fehler nicht verschlucken.
    monkeypatch.setattr(records_mod, "_current_row", lambda *a, **k: None)
    with pytest.raises(IntegrityError, match="record_current"):
        write_records(
            conn,
            entity="book",
            rows=[RecordRow("k", {"a": 2}, snap)],
            site_pack_id=sp,
            run_id=run1,
            valid_from=T2,
        )
    conn.rollback()
