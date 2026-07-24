"""Incident-Writer: Dedup, Alarmstufen-Mapping, Evidence (I1.4.5, N01-N06)."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from kintsugi.classify.enums import IncidentKind
from kintsugi.classify.outcome import INCIDENT_SEVERITY
from kintsugi.storage.db import get_engine
from kintsugi.storage.incidents import report
from kintsugi.storage.tables import run as run_table
from kintsugi.storage.tables import site_pack
from sqlalchemy import Connection, insert, text

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def _pack(conn: Connection) -> UUID:
    return conn.execute(
        insert(site_pack)
        .values(
            domain="books.toscrape.com",
            entity="book",
            version=1,
            spec={"domain": "books.toscrape.com"},
            created_by="human:test",
        )
        .returning(site_pack.c.id)
    ).scalar_one()


def _run(conn: Connection, sp: UUID) -> UUID:
    return conn.execute(
        insert(run_table)
        .values(site_pack_id=sp, trigger="manual", status="running")
        .returning(run_table.c.id)
    ).scalar_one()


# docs/06-Alarmstufen-Abbildung (N01, N02, N04-N06 plus Kontingent).
_MAPPING = [
    (IncidentKind.blocked, "warn"),
    (IncidentKind.unreachable, "warn"),
    (IncidentKind.rate_limited, "warn"),
    (IncidentKind.fill_rate_drop, "warn"),
    (IncidentKind.row_count_anomaly, "info"),
    (IncidentKind.healer_exhausted, "critical"),
]


@pytest.mark.parametrize(("kind", "severity"), _MAPPING)
def test_kind_und_severity_landen_in_postgres(conn, kind, severity):
    # Die Severity ist die eine docs/06-Tabelle, nicht ein Aufrufer-Freiheitsgrad.
    assert INCIDENT_SEVERITY[kind] == severity
    sp = _pack(conn)
    run_id = _run(conn, sp)
    incident_id = report(
        conn,
        site_pack_id=sp,
        run_id=run_id,
        kind=kind,
        severity=severity,
        field="price" if kind is IncidentKind.fill_rate_drop else None,
        evidence={"url": "https://books.toscrape.com/x"},
    )
    conn.commit()
    row = conn.execute(
        text("select kind, severity from incident where id = :id"), {"id": incident_id}
    ).one()
    assert row.kind == kind.value
    assert row.severity == severity


def test_wiederholte_meldung_dedupliziert_und_zaehlt_hoch(conn):
    sp = _pack(conn)
    run_id = _run(conn, sp)
    for i in (1, 2, 3):
        report(
            conn,
            site_pack_id=sp,
            run_id=run_id,
            kind=IncidentKind.fill_rate_drop,
            severity="warn",
            field="price",
            evidence={"snapshot_id": f"snap-{i}", "url": "https://books.toscrape.com/x"},
        )
        conn.commit()

    open_count = conn.execute(
        text("select count(*) from incident where closed_at is null")
    ).scalar_one()
    assert open_count == 1

    row = conn.execute(
        text(
            "select evidence->>'occurrences' as occ, evidence->>'snapshot_id' as snap "
            "from incident where closed_at is null"
        )
    ).one()
    assert row.occ == "3"
    assert row.snap == "snap-3"  # die letzte Meldung gewinnt


def test_verschiedenes_feld_ist_ein_eigener_incident(conn):
    sp = _pack(conn)
    run_id = _run(conn, sp)
    for field in ("price", "title"):
        report(
            conn,
            site_pack_id=sp,
            run_id=run_id,
            kind=IncidentKind.fill_rate_drop,
            severity="warn",
            field=field,
            evidence={"url": "https://books.toscrape.com/x"},
        )
    conn.commit()
    open_count = conn.execute(
        text("select count(*) from incident where closed_at is null")
    ).scalar_one()
    assert open_count == 2


def test_rate_limited_evidence_traegt_die_pflichtfelder(conn):
    sp = _pack(conn)
    run_id = _run(conn, sp)
    report(
        conn,
        site_pack_id=sp,
        run_id=run_id,
        kind=IncidentKind.rate_limited,
        severity="warn",
        field=None,
        evidence={
            "url": "https://books.toscrape.com/x",
            "http_status": 429,
            "matched_signature": {"id": "http_429", "pattern": "status:429"},
            "headers": {"retry-after": "30", "server": "nginx"},
            "retry_after_s": 30,
            "recommended_rps": 0.25,
        },
    )
    conn.commit()
    row = conn.execute(
        text(
            "select evidence->>'url' as url, "
            "evidence->'matched_signature'->>'id' as sig, "
            "evidence->>'http_status' as status, "
            "evidence->'headers'->>'retry-after' as retry, "
            "evidence->>'recommended_rps' as rps "
            "from incident where closed_at is null"
        )
    ).one()
    assert row.url == "https://books.toscrape.com/x"
    assert row.sig == "http_429"
    assert row.status == "429"
    assert row.retry == "30"
    assert row.rps == "0.25"


def test_geschlossener_incident_blockiert_neuen_nicht(conn):
    sp = _pack(conn)
    run_id = _run(conn, sp)
    first = report(
        conn,
        site_pack_id=sp,
        run_id=run_id,
        kind=IncidentKind.blocked,
        severity="warn",
        field=None,
        evidence={"url": "https://books.toscrape.com/x"},
    )
    conn.execute(
        text(
            "update incident set closed_at = now(), resolution = 'source_recovered' where id = :id"
        ),
        {"id": first},
    )
    second = report(
        conn,
        site_pack_id=sp,
        run_id=run_id,
        kind=IncidentKind.blocked,
        severity="warn",
        field=None,
        evidence={"url": "https://books.toscrape.com/x"},
    )
    conn.commit()
    assert first != second  # der geschlossene wird nicht wiederverwendet
    total = conn.execute(text("select count(*) from incident")).scalar_one()
    assert total == 2
