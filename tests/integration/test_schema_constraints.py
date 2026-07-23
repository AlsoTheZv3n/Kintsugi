"""CHECK- und Unique-Constraints des Schemas (I0.4.4-I0.4.7). Braucht postgres:16.

CHECK-Constraints liegen ausserhalb des Autogenerate-Drift-Waechters; diese
Tests sind ihr Nachweis. Jeder prueft, dass die Datenbank eine ungueltige Zeile
ablehnt — die Invarianten aus docs/03 werden in der Datenbank erzwungen, nicht
in der Anwendung.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from kintsugi.storage.db import get_engine
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

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
    """Jeder Test in einer eigenen Transaktion, am Ende zurueckgerollt."""
    with migrated_engine.connect() as connection:
        trans = connection.begin()
        try:
            yield connection
        finally:
            trans.rollback()


def _new_site_pack(
    conn: Connection,
    *,
    domain="books.toscrape.com",
    status="draft",
    version=1,
    created_by="human:sven",
    activated=False,
) -> uuid.UUID:
    row = conn.execute(
        text(
            "INSERT INTO site_pack (domain, entity, version, status, spec, created_by, "
            "activated_at) VALUES (:d, 'book', :v, CAST(:s AS site_pack_status), "
            "CAST(:spec AS jsonb), :cb, :act) RETURNING id"
        ),
        {
            "d": domain,
            "v": version,
            "s": status,
            "spec": f'{{"domain": "{domain}"}}',
            "cb": created_by,
            "act": "2026-07-21T00:00:00Z" if activated else None,
        },
    )
    return row.scalar_one()


def _new_run(conn: Connection, site_pack_id: uuid.UUID) -> uuid.UUID:
    return conn.execute(
        text("INSERT INTO run (site_pack_id, trigger) VALUES (:sp, 'manual') RETURNING id"),
        {"sp": site_pack_id},
    ).scalar_one()


def _new_snapshot(conn: Connection, run_id: uuid.UUID) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO snapshot (run_id, url, http_status, content_hash, byte_size, "
            "blob_key, fetcher) VALUES (:r, 'https://x/1', 200, :h, 10, 'raw/x', 'httpx') "
            "RETURNING id"
        ),
        {"r": run_id, "h": b"\x00" * 32},
    ).scalar_one()


# ---------------------------------------------------------------- site_pack (0002)


def test_zwei_aktive_versionen_kollidieren(conn):
    _new_site_pack(conn, status="active", version=1, activated=True)
    with pytest.raises(IntegrityError, match="site_pack_one_active"):
        _new_site_pack(conn, status="active", version=2, activated=True)


def test_zwei_retired_versionen_sind_erlaubt(conn):
    _new_site_pack(conn, status="retired", version=1)
    _new_site_pack(conn, status="retired", version=2)  # keine Kollision


def test_zwei_canary_versionen_kollidieren(conn):
    _new_site_pack(conn, status="canary", version=1)
    with pytest.raises(IntegrityError, match="site_pack_one_canary"):
        _new_site_pack(conn, status="canary", version=2)


def test_spec_domain_muss_zur_spalte_passen(conn):
    with pytest.raises(IntegrityError, match="spec_domain_matches"):
        conn.execute(
            text(
                "INSERT INTO site_pack (domain, entity, version, spec, created_by) "
                "VALUES ('a.test', 'book', 1, '{\"domain\": \"b.test\"}', 'human:sven')"
            )
        )


def test_created_by_ohne_namespace_wird_abgelehnt(conn):
    with pytest.raises(IntegrityError, match="created_by_namespaced"):
        _new_site_pack(conn, created_by="sven")


@pytest.mark.parametrize("cb", ["healer:v1", "human:sven"])
def test_created_by_mit_namespace_ist_erlaubt(conn, cb):
    _new_site_pack(conn, created_by=cb)


def test_aktiv_ohne_aktivierungszeitpunkt_wird_abgelehnt(conn):
    with pytest.raises(IntegrityError, match="active_needs_activation"):
        _new_site_pack(conn, status="active", activated=False)


# ---------------------------------------------------------------- run (0003)


def test_terminaler_status_ohne_finished_wird_abgelehnt(conn):
    sp = _new_site_pack(conn)
    with pytest.raises(IntegrityError, match="terminal_needs_finished"):
        conn.execute(
            text("INSERT INTO run (site_pack_id, trigger, status) VALUES (:sp, 'manual', 'ok')"),
            {"sp": sp},
        )


def test_finished_vor_started_wird_abgelehnt(conn):
    sp = _new_site_pack(conn)
    with pytest.raises(IntegrityError, match="finished_after_started"):
        conn.execute(
            text(
                "INSERT INTO run (site_pack_id, trigger, status, started_at, finished_at) "
                "VALUES (:sp, 'manual', 'ok', '2026-07-21T10:00:00Z', '2026-07-21T09:00:00Z')"
            ),
            {"sp": sp},
        )


# ---------------------------------------------------------------- snapshot (0003)


def test_unbekannter_fetcher_wird_abgelehnt(conn):
    sp = _new_site_pack(conn)
    r = _new_run(conn, sp)
    with pytest.raises(IntegrityError, match="fetcher_known"):
        conn.execute(
            text(
                "INSERT INTO snapshot (run_id, url, http_status, content_hash, byte_size, "
                "blob_key, fetcher) VALUES (:r, 'https://x/1', 200, :h, 10, 'raw/x', 'curl')"
            ),
            {"r": r, "h": b"\x00" * 32},
        )


def test_http_404_ist_speicherbar(conn):
    """F1: die Paginierungs-Endmarke (page-51 -> 404) muss ablegbar sein."""
    sp = _new_site_pack(conn)
    r = _new_run(conn, sp)
    conn.execute(
        text(
            "INSERT INTO snapshot (run_id, url, http_status, content_hash, byte_size, "
            "blob_key, fetcher) VALUES (:r, 'https://x/51', 404, :h, 10, 'raw/x', 'httpx')"
        ),
        {"r": r, "h": b"\x11" * 32},
    )


def test_golden_ohne_label_wird_abgelehnt(conn):
    sp = _new_site_pack(conn)
    r = _new_run(conn, sp)
    with pytest.raises(IntegrityError, match="golden_needs_label"):
        conn.execute(
            text(
                "INSERT INTO snapshot (run_id, url, http_status, content_hash, byte_size, "
                "blob_key, fetcher, is_golden) VALUES (:r, 'https://x/1', 200, :h, 10, "
                "'raw/x', 'httpx', true)"
            ),
            {"r": r, "h": b"\x22" * 32},
        )


# ---------------------------------------------------------------- record (0004)


def _record_values(conn: Connection) -> tuple[uuid.UUID, uuid.UUID]:
    sp = _new_site_pack(conn)
    r = _new_run(conn, sp)
    s = _new_snapshot(conn, r)
    return sp, s


def test_zwei_aktuelle_records_gleicher_key_kollidieren(conn):
    sp, s = _record_values(conn)
    conn.execute(
        text(
            "INSERT INTO record (entity, natural_key, snapshot_id, site_pack_id, payload, "
            "payload_hash) VALUES ('book', 'upc1', :s, :sp, '{}', :h)"
        ),
        {"s": s, "sp": sp, "h": b"\x00" * 32},
    )
    with pytest.raises(IntegrityError, match="record_current"):
        conn.execute(
            text(
                "INSERT INTO record (entity, natural_key, snapshot_id, site_pack_id, payload, "
                "payload_hash) VALUES ('book', 'upc1', :s, :sp, '{}', :h)"
            ),
            {"s": s, "sp": sp, "h": b"\x01" * 32},
        )


def test_historisierte_zeile_gibt_den_key_frei(conn):
    sp, s = _record_values(conn)
    first = conn.execute(
        text(
            "INSERT INTO record (entity, natural_key, snapshot_id, site_pack_id, payload, "
            "payload_hash) VALUES ('book', 'upc1', :s, :sp, '{}', :h) RETURNING id"
        ),
        {"s": s, "sp": sp, "h": b"\x00" * 32},
    ).scalar_one()
    # now() ist innerhalb der Transaktion eingefroren (= valid_from). In echtem
    # SCD-2 ist valid_to der spaetere Zeitstempel des Folgelaufs; hier explizit.
    conn.execute(
        text("UPDATE record SET valid_to = valid_from + interval '1 second' WHERE id = :i"),
        {"i": first},
    )
    conn.execute(  # jetzt erlaubt
        text(
            "INSERT INTO record (entity, natural_key, snapshot_id, site_pack_id, payload, "
            "payload_hash) VALUES ('book', 'upc1', :s, :sp, '{}', :h)"
        ),
        {"s": s, "sp": sp, "h": b"\x01" * 32},
    )


def test_valid_to_vor_valid_from_wird_abgelehnt(conn):
    sp, s = _record_values(conn)
    with pytest.raises(IntegrityError, match="valid_interval"):
        conn.execute(
            text(
                "INSERT INTO record (entity, natural_key, snapshot_id, site_pack_id, payload, "
                "payload_hash, valid_from, valid_to) VALUES ('book', 'upc1', :s, :sp, '{}', "
                ":h, '2026-07-21T10:00:00Z', '2026-07-21T09:00:00Z')"
            ),
            {"s": s, "sp": sp, "h": b"\x00" * 32},
        )


def test_gold_book_hat_acht_spalten(conn):
    cols = conn.execute(text("SELECT * FROM gold_book LIMIT 0")).keys()
    assert list(cols) == [
        "upc",
        "title",
        "price",
        "currency",
        "availability",
        "updated_at",
        "source_domain",
        "extractor_version",
    ]


def test_gold_book_currency_kommt_aus_dem_payload(conn):
    """F3: currency wird abgeleitet; die View muss sie nicht-null liefern."""
    sp, s = _record_values(conn)
    conn.execute(
        text(
            "INSERT INTO record (entity, natural_key, snapshot_id, site_pack_id, payload, "
            "payload_hash) VALUES ('book', 'upc1', :s, :sp, "
            '\'{"title": "T", "price": "51.77", "currency": "GBP"}\', :h)'
        ),
        {"s": s, "sp": sp, "h": b"\x00" * 32},
    )
    assert conn.execute(text("SELECT currency FROM gold_book")).scalar_one() == "GBP"


# ---------------------------------------------------------------- incident (0005)


def test_zwei_offene_incidents_gleicher_art_kollidieren(conn):
    sp = _new_site_pack(conn)
    conn.execute(
        text(
            "INSERT INTO incident (site_pack_id, kind, severity, field) "
            "VALUES (:sp, 'fill_rate_drop', 'warn', 'price')"
        ),
        {"sp": sp},
    )
    with pytest.raises(IntegrityError, match="incident_open_dedup"):
        conn.execute(
            text(
                "INSERT INTO incident (site_pack_id, kind, severity, field) "
                "VALUES (:sp, 'fill_rate_drop', 'warn', 'price')"
            ),
            {"sp": sp},
        )


def test_dedup_greift_auch_bei_field_null(conn):
    """NULLS NOT DISTINCT: zwei offene Incidents mit field IS NULL kollidieren."""
    sp = _new_site_pack(conn)
    conn.execute(
        text("INSERT INTO incident (site_pack_id, kind, severity) VALUES (:sp, 'blocked', 'warn')"),
        {"sp": sp},
    )
    with pytest.raises(IntegrityError, match="incident_open_dedup"):
        conn.execute(
            text(
                "INSERT INTO incident (site_pack_id, kind, severity) "
                "VALUES (:sp, 'blocked', 'warn')"
            ),
            {"sp": sp},
        )


def test_geschlossener_incident_gibt_die_dedup_frei(conn):
    sp = _new_site_pack(conn)
    first = conn.execute(
        text(
            "INSERT INTO incident (site_pack_id, kind, severity, field) "
            "VALUES (:sp, 'fill_rate_drop', 'warn', 'price') RETURNING id"
        ),
        {"sp": sp},
    ).scalar_one()
    conn.execute(
        text("UPDATE incident SET closed_at = now(), resolution = 'human_fixed' WHERE id = :i"),
        {"i": first},
    )
    conn.execute(  # jetzt erlaubt
        text(
            "INSERT INTO incident (site_pack_id, kind, severity, field) "
            "VALUES (:sp, 'fill_rate_drop', 'warn', 'price')"
        ),
        {"sp": sp},
    )


def test_geschlossen_ohne_resolution_wird_abgelehnt(conn):
    sp = _new_site_pack(conn)
    with pytest.raises(IntegrityError, match="closure_needs_resolution"):
        conn.execute(
            text(
                "INSERT INTO incident (site_pack_id, kind, severity, closed_at) "
                "VALUES (:sp, 'blocked', 'warn', now())"
            ),
            {"sp": sp},
        )


def test_resolution_ohne_closed_wird_abgelehnt(conn):
    sp = _new_site_pack(conn)
    with pytest.raises(IntegrityError, match="closure_needs_resolution"):
        conn.execute(
            text(
                "INSERT INTO incident (site_pack_id, kind, severity, resolution) "
                "VALUES (:sp, 'blocked', 'warn', 'human_fixed')"
            ),
            {"sp": sp},
        )


def test_incident_ohne_run_ist_erlaubt(conn):
    """Ein vom Probe ausgeloester Incident haengt an keinem Lauf."""
    sp = _new_site_pack(conn)
    conn.execute(
        text(
            "INSERT INTO incident (site_pack_id, kind, severity, run_id) "
            "VALUES (:sp, 'unreachable', 'warn', NULL)"
        ),
        {"sp": sp},
    )
