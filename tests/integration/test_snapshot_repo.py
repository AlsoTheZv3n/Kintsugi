"""save_snapshot: write-before-parse, 304-Zeilenform, versionsbewusst (I0.9.2)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from alembic import command
from alembic.config import Config
from kintsugi.fetch.base import FetchOutcome, FetchResult
from kintsugi.storage.db import get_engine
from kintsugi.storage.snapshot_repo import save_snapshot
from kintsugi.storage.snapshots import FilesystemSnapshotStore
from kintsugi.storage.tables import record, run, site_pack, snapshot
from sqlalchemy import Connection, func, insert, select, text

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
URL = "https://books.toscrape.com/catalogue/x_1/index.html"
DOMAIN = "books.toscrape.com"


def _make_result(url=URL, *, status=200, body=b"<html>book</html>", headers=None):
    outcome = FetchOutcome.not_modified if status == 304 else FetchOutcome.ok
    return FetchResult(
        url=url,
        final_url=url,
        http_status=status,
        headers=headers or {},
        body=body,
        content_type="text/html",
        encoding="utf-8",
        elapsed_ms=1,
        fetcher="httpx",
        from_cache=status == 304,
        outcome=outcome,
    )


class FakeFetcher:
    """Gibt vorgegebene Ergebnisse zurueck; Exceptions werden geworfen."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple] = []

    def fetch(self, url, *, etag=None, last_modified=None):
        self.calls.append((url, etag, last_modified))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


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
def store(tmp_path):
    return FilesystemSnapshotStore(tmp_path)


def _seed_pack(conn: Connection, version: int = 1) -> UUID:
    return conn.execute(
        insert(site_pack)
        .values(
            domain=DOMAIN,
            entity="book",
            version=version,
            spec={"domain": DOMAIN},
            created_by="human:sven",
        )
        .returning(site_pack.c.id)
    ).scalar_one()


def _seed_run(conn: Connection, sp: UUID) -> UUID:
    return conn.execute(
        insert(run).values(site_pack_id=sp, trigger="manual").returning(run.c.id)
    ).scalar_one()


def _count_snapshots(conn: Connection, url: str) -> int:
    return conn.execute(
        select(func.count()).select_from(snapshot).where(snapshot.c.url == url)
    ).scalar_one()


def test_unveraendert_schreibt_zweite_zeile_aber_einen_blob(conn, store, tmp_path):
    sp = _seed_pack(conn)
    run_id = _seed_run(conn, sp)
    conn.commit()
    body = b"<html>identisch</html>"
    fetcher = FakeFetcher([_make_result(body=body), _make_result(body=body)])

    r1 = save_snapshot(
        conn, run_id=run_id, url=URL, fetcher=fetcher, store=store, site_pack_id=sp, entity="book"
    )
    r2 = save_snapshot(
        conn, run_id=run_id, url=URL, fetcher=fetcher, store=store, site_pack_id=sp, entity="book"
    )

    assert _count_snapshots(conn, URL) == 2
    hashes = conn.execute(
        select(snapshot.c.content_hash, snapshot.c.blob_key).where(snapshot.c.url == URL)
    ).all()
    assert hashes[0].content_hash == hashes[1].content_hash
    assert hashes[0].blob_key == hashes[1].blob_key == r1.blob_key == r2.blob_key
    assert len(list(Path(tmp_path).rglob("*.gz"))) == 1


def test_write_before_parse_ueberlebt_extraktor_fehler(conn, store, engine):
    sp = _seed_pack(conn)
    run_id = _seed_run(conn, sp)
    conn.commit()
    fetcher = FakeFetcher([_make_result()])
    save_snapshot(
        conn, run_id=run_id, url=URL, fetcher=fetcher, store=store, site_pack_id=sp, entity="book"
    )

    def extractor():
        raise RuntimeError("extraktor kracht nach der Persistenz")

    with pytest.raises(RuntimeError):
        extractor()

    # Frische Verbindung: die Zeile ist committet, unabhaengig vom Extraktor.
    with engine.connect() as fresh:
        assert _count_snapshots(fresh, URL) == 1


def test_version_aware_unchanged(conn, store):
    sp1 = _seed_pack(conn, 1)
    sp2 = _seed_pack(conn, 2)
    run_id = _seed_run(conn, sp2)
    body = b"<html>stabil</html>"
    content_hash = hashlib.sha256(body).digest()
    blob_key = store.build_key(DOMAIN, datetime.now(UTC), content_hash)
    store.put(blob_key, body)
    prior_snap = conn.execute(
        insert(snapshot)
        .values(
            run_id=run_id,
            url=URL,
            http_status=200,
            content_hash=content_hash,
            byte_size=len(body),
            blob_key=blob_key,
            fetcher="httpx",
        )
        .returning(snapshot.c.id)
    ).scalar_one()
    conn.execute(
        insert(record).values(
            entity="book",
            natural_key="upc-x",
            snapshot_id=prior_snap,
            site_pack_id=sp1,
            payload={"upc": "upc-x"},
            payload_hash=b"\x00" * 32,
        )
    )
    conn.commit()

    # Aktives Pack == das Pack der aktuellen record-Zeile -> unchanged.
    r_match = save_snapshot(
        conn,
        run_id=run_id,
        url=URL,
        fetcher=FakeFetcher([_make_result(body=body)]),
        store=store,
        site_pack_id=sp1,
        entity="book",
    )
    assert r_match.unchanged is True

    # Andere Pack-Version -> nicht unchanged (geheilter Selektor muss durch).
    r_diff = save_snapshot(
        conn,
        run_id=run_id,
        url=URL,
        fetcher=FakeFetcher([_make_result(body=body)]),
        store=store,
        site_pack_id=sp2,
        entity="book",
    )
    assert r_diff.unchanged is False

    # canary-Trigger -> nie unchanged.
    r_canary = save_snapshot(
        conn,
        run_id=run_id,
        url=URL,
        fetcher=FakeFetcher([_make_result(body=body)]),
        store=store,
        site_pack_id=sp1,
        entity="book",
        trigger="canary",
    )
    assert r_canary.unchanged is False


def test_connect_error_schreibt_keine_zeile_aber_run_error(conn, store, engine):
    sp = _seed_pack(conn)
    run_id = _seed_run(conn, sp)
    conn.commit()
    fetcher = FakeFetcher([httpx.ConnectError("keine Route zum Host")])

    r = save_snapshot(
        conn, run_id=run_id, url=URL, fetcher=fetcher, store=store, site_pack_id=sp, entity="book"
    )
    assert r.snapshot_id is None
    assert r.outcome is FetchOutcome.error
    with engine.connect() as fresh:
        assert _count_snapshots(fresh, URL) == 0
        err = fresh.execute(select(run.c.error).where(run.c.id == run_id)).scalar_one()
    assert err is not None
    assert "fetch_error" in err


def test_304_zeilenform_und_body_aus_store(conn, store):
    sp = _seed_pack(conn)
    run_id = _seed_run(conn, sp)
    body = b"<html>zweihundert</html>"
    content_hash = hashlib.sha256(body).digest()
    blob_key = store.build_key(DOMAIN, datetime.now(UTC), content_hash)
    store.put(blob_key, body)
    conn.execute(
        insert(snapshot).values(
            run_id=run_id,
            url=URL,
            # Frueherer Lauf: der 304 unten muss zeitlich danach liegen, damit
            # "juengster Snapshot" deterministisch die 304-Zeile ist.
            fetched_at=datetime(2026, 7, 20, tzinfo=UTC),
            http_status=200,
            content_hash=content_hash,
            byte_size=len(body),
            blob_key=blob_key,
            fetcher="httpx",
            etag='"abc"',
            last_modified="Mon, 20 Jul 2026 00:00:00 GMT",
        )
    )
    conn.commit()

    fetcher = FakeFetcher([_make_result(status=304, body=b"")])
    r = save_snapshot(
        conn, run_id=run_id, url=URL, fetcher=fetcher, store=store, site_pack_id=sp, entity="book"
    )

    # Die Anfrage war bedingt: der gespeicherte ETag ging als If-None-Match raus.
    assert fetcher.calls[0][1] == '"abc"'
    assert r.http_status == 304
    assert r.outcome is FetchOutcome.not_modified
    assert r.body == body  # aus dem Store nachgeladen

    latest = conn.execute(
        select(
            snapshot.c.http_status,
            snapshot.c.byte_size,
            snapshot.c.content_hash,
            snapshot.c.blob_key,
        )
        .where(snapshot.c.url == URL)
        .order_by(snapshot.c.fetched_at.desc())
        .limit(1)
    ).one()
    assert latest.http_status == 304
    assert latest.byte_size == 0
    assert latest.content_hash == content_hash
    assert latest.blob_key == blob_key
