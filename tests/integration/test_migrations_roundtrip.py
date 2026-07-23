"""Migrationen sind reversibel und driftfrei (I0.4.2). Braucht postgres:16.

Der Round-Trip upgrade->downgrade->upgrade laeuft **zweimal** auf derselben
Datenbank: das faengt ein downgrade(), das ein DROP TYPE vergessen hat — beim
zweiten upgrade schluege das CREATE TYPE sonst mit "type already exists" fehl.

Der Drift-Test vergleicht die Metadata gegen die Datenbank am head. Er ist
bewusst schmal: compare_server_default=False, und CHECK-Constraints liegen
ausserhalb (Autogenerate vergleicht sie nicht) und sind durch die
Verletzungstests der einzelnen Migrationen gedeckt.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from kintsugi.storage.db import get_engine
from kintsugi.storage.tables import metadata
from migrations.include import include_object
from sqlalchemy import text

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Indizes auf der env.py-Allowlist muessen am head existieren.
ALLOWLISTED_INDEXES = [
    "site_pack_one_active",
    "site_pack_one_canary",
    "snapshot_golden",
    "record_current",
    "incident_open",
]


@pytest.fixture(scope="module")
def engine():
    eng = get_engine()
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"kein postgres erreichbar: {exc}")
    return eng


@pytest.fixture
def alembic_config(engine):
    """Frische, leere Datenbank plus eine Alembic-Config darauf."""
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return cfg


def test_upgrade_downgrade_upgrade_zweimal(alembic_config, engine):
    """Zweimal, damit ein vergessenes DROP im downgrade auffliegt."""
    for _ in range(2):
        command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    with engine.connect() as conn:
        tables = set(
            conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        )
    assert {"site_pack", "run", "snapshot", "record", "incident"} <= tables


def test_kein_drift_am_head(alembic_config, engine):
    command.upgrade(alembic_config, "head")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "compare_type": True,
                "compare_server_default": False,
                "include_object": include_object,
            },
        )
        diff = compare_metadata(ctx, metadata)
    assert diff == [], f"Autogenerate meldet Drift: {diff}"


def test_allowlist_indizes_existieren_am_head(alembic_config, engine):
    command.upgrade(alembic_config, "head")
    with engine.connect() as conn:
        present = set(
            conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            ).scalars()
        )
    fehlen = [ix for ix in ALLOWLISTED_INDEXES if ix not in present]
    assert not fehlen, f"Allowlist-Indizes fehlen am head: {fehlen}"
