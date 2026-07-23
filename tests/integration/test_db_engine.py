"""Integrationstest fuer die Engine (I0.4.1). Braucht ein laufendes postgres:16.

Erreicht die Datenbank nur ueber den Treiber — es gibt keinen lokalen
psql-Client (F6).
"""

from __future__ import annotations

import pytest
from kintsugi.storage.db import current_timezone, get_engine, transaction
from sqlalchemy import text

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine():
    eng = get_engine()
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"kein postgres erreichbar: {exc}")
    return eng


def test_sitzung_laeuft_in_utc(engine):
    """timestamptz soll auf Windows und Linux identisch round-trippen."""
    with engine.connect() as conn:
        assert current_timezone(conn) == "UTC"


def test_transaction_committet_bei_sauberem_austritt(engine):
    with transaction(engine) as conn:
        conn.execute(text("CREATE TEMP TABLE probe_commit (n int) ON COMMIT DROP"))
        # Innerhalb derselben Transaktion sichtbar.
        assert conn.execute(text("SELECT count(*) FROM probe_commit")).scalar_one() == 0


def test_transaction_rollt_bei_ausnahme_zurueck(engine):
    """Eine geworfene Ausnahme darf keine Zeile hinterlassen."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS probe_rollback"))
        conn.execute(text("CREATE TABLE probe_rollback (n int)"))
    try:
        with transaction(engine) as conn:
            conn.execute(text("INSERT INTO probe_rollback (n) VALUES (1)"))
            raise RuntimeError("absichtlich")
    except RuntimeError:
        pass

    # Frische Verbindung: die zurueckgerollte Zeile darf nicht sichtbar sein.
    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM probe_rollback")).scalar_one()
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE probe_rollback"))
    assert count == 0, "Rollback hat die Zeile nicht verworfen"


def test_falscher_treiber_wird_abgelehnt():
    """ADR-008: nur der synchrone psycopg3-Treiber ist erlaubt."""
    from kintsugi.config import Settings
    from kintsugi.storage import db

    bad = Settings(database_url_override="postgresql+asyncpg://u:p@h/db")
    with pytest.raises(ValueError, match="psycopg"):
        db.get_engine(bad)
