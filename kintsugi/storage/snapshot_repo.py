"""save_snapshot: Bronze entsteht vor dem Parsing (ADR-009).

docs/01-architecture.md §Datenfluss ("Der Snapshot entsteht vor dem Parsing",
ausdruecklich nicht verhandelbar) und docs/03-data-model.md §Bronze. Die
Reihenfolge ist fix: sha256 des Rohkoerpers bilden, blob_key bauen, Blob
schreiben (uebersprungen, wenn er existiert), snapshot-Zeile einfuegen,
committen — *dann* zurueckgeben. Nichts parst, bevor die Zeile existiert.

Zwei ADR-009-Entscheidungen sitzen hier:

1. **Unveraenderter content_hash schreibt trotzdem eine snapshot-Zeile.**
   ``record.snapshot_id`` ist NOT NULL und die Phase-0-DoD prueft genau den
   zweiten Lauf — ohne frische snapshot-Zeile haette er nichts, worauf er zeigt.
   Blob-Schreiben und Extraktion entfallen, der vorhandene ``blob_key`` wird
   wiederverwendet.
2. **Der Extraktions-Kurzschluss ist versionsbewusst.** ``unchanged`` gilt nur,
   wenn die aktive ``site_pack_id`` die ist, die die aktuelle ``record``-Zeile
   fuer diese URL erzeugt hat, und der Trigger nicht ``canary``/``replay`` ist.
   Sonst erreichte ein in Phase 2 promoteter geheilter Selektor nie
   unveraenderte Seiten.

Konventionen: ein Abruf ohne HTTP-Antwort (DNS, Connect-Timeout, TLS) schreibt
**keine** Zeile; er wird an ``run.error`` angehaengt. Ein ``304`` schreibt eine
Zeile mit ``byte_size = 0`` und traegt ``content_hash``/``blob_key`` vom letzten
``200`` nach; der Body kommt aus dem Store. Ein ``304`` ohne vorheriges ``200``
ist wertlos (es gaebe nichts nachzutragen) und loest genau einen unbedingten
Neuversuch aus.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
from sqlalchemy import Row, func, insert, select, update

from kintsugi.fetch.base import FetchOutcome
from kintsugi.storage.blobkey import domain_of
from kintsugi.storage.tables import record, run, snapshot

if TYPE_CHECKING:
    from sqlalchemy import Connection

    from kintsugi.fetch.base import Fetcher
    from kintsugi.storage.snapshots import SnapshotStore

__all__ = ["SnapshotResult", "save_snapshot"]

_EXCLUDED_TRIGGERS = frozenset({"canary", "replay"})


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    """Was der Runner nach der Persistenz weiss.

    ``snapshot_id`` ist ``None``, wenn keine Zeile geschrieben wurde (kein
    HTTP-Response oder haengendes 304). ``unchanged`` heisst versionsbewusst
    unveraendert — der Runner darf die Extraktion ueberspringen. ``body`` ist
    der unkomprimierte Koerper (bei 304 aus dem Store nachgeladen).
    """

    snapshot_id: UUID | None
    blob_key: str | None
    unchanged: bool
    body: bytes | None
    http_status: int | None
    outcome: FetchOutcome
    error: str | None = None


def _append_run_error(conn: Connection, run_id: UUID, message: str) -> None:
    # concat_ws ueberspringt NULL: erste Meldung steht allein, weitere je Zeile.
    conn.execute(
        update(run)
        .where(run.c.id == run_id)
        .values(error=func.concat_ws("\n", run.c.error, message))
    )


def _last_snapshot(conn: Connection, url: str) -> Row[Any] | None:
    return conn.execute(
        select(
            snapshot.c.content_hash,
            snapshot.c.blob_key,
            snapshot.c.etag,
            snapshot.c.last_modified,
        )
        .where(snapshot.c.url == url)
        .order_by(snapshot.c.fetched_at.desc())
        .limit(1)
    ).first()


def _last_ok_snapshot(conn: Connection, url: str) -> Row[Any] | None:
    return conn.execute(
        select(
            snapshot.c.content_hash,
            snapshot.c.blob_key,
            snapshot.c.etag,
            snapshot.c.last_modified,
        )
        .where(snapshot.c.url == url, snapshot.c.http_status == 200)
        .order_by(snapshot.c.fetched_at.desc())
        .limit(1)
    ).first()


def _blob_key_for_hash(conn: Connection, content_hash: bytes) -> str | None:
    return conn.execute(
        select(snapshot.c.blob_key).where(snapshot.c.content_hash == content_hash).limit(1)
    ).scalar_one_or_none()


def _current_record_pack(conn: Connection, entity: str, url: str) -> UUID | None:
    """Die ``site_pack_id``, die die aktuelle record-Zeile fuer diese URL erzeugte."""
    joined = record.join(snapshot, snapshot.c.id == record.c.snapshot_id)
    return conn.execute(
        select(record.c.site_pack_id)
        .select_from(joined)
        .where(record.c.entity == entity, record.c.valid_to.is_(None), snapshot.c.url == url)
        .limit(1)
    ).scalar_one_or_none()


def save_snapshot(
    conn: Connection,
    *,
    run_id: UUID,
    url: str,
    fetcher: Fetcher,
    store: SnapshotStore,
    site_pack_id: UUID,
    entity: str,
    trigger: str = "manual",
    conditional_requests: bool = True,
) -> SnapshotResult:
    prior_any = _last_snapshot(conn, url)
    prior_ok = _last_ok_snapshot(conn, url)

    etag = prior_ok.etag if (conditional_requests and prior_ok is not None) else None
    last_modified = (
        prior_ok.last_modified if (conditional_requests and prior_ok is not None) else None
    )

    try:
        result = fetcher.fetch(url, etag=etag, last_modified=last_modified)
    except httpx.RequestError as exc:
        message = f"fetch_error:{type(exc).__name__}:{url}"
        _append_run_error(conn, run_id, message)
        conn.commit()
        return SnapshotResult(None, None, False, None, None, FetchOutcome.error, message)

    # Haengendes 304: nichts nachzutragen. Genau ein unbedingter Neuversuch,
    # damit eine Zeile nie auf einen nie geschriebenen Blob zeigt.
    if result.http_status == 304 and prior_ok is None:
        result = fetcher.fetch(url)
        if result.http_status == 304:
            message = f"dangling_304:{url}"
            _append_run_error(conn, run_id, message)
            conn.commit()
            return SnapshotResult(None, None, False, None, None, result.outcome, message)

    fetched_at = datetime.now(UTC)

    if result.http_status == 304 and prior_ok is not None:
        # 304-Zeilenform: byte_size 0, content_hash/blob_key vom letzten 200.
        content_hash = prior_ok.content_hash
        blob_key = prior_ok.blob_key
        byte_size = 0
        body = store.get(blob_key)
        etag_store = prior_ok.etag
        last_modified_store = prior_ok.last_modified
    else:
        body = result.body
        content_hash = hashlib.sha256(body).digest()
        # Inhaltsadressiert: existierenden blob_key ueber content_hash
        # wiederverwenden, sonst laege identischer Inhalt doppelt unter zwei
        # Monatspartitionen (blobkey.py §Monat).
        existing = _blob_key_for_hash(conn, content_hash)
        blob_key = existing or store.build_key(domain_of(url), fetched_at, content_hash)
        byte_size = len(body)
        store.put(blob_key, body)  # No-op, wenn der Schluessel schon existiert.
        etag_store = result.headers.get("etag")
        last_modified_store = result.headers.get("last-modified")

    snapshot_id = conn.execute(
        insert(snapshot)
        .values(
            run_id=run_id,
            url=url,
            fetched_at=fetched_at,
            http_status=result.http_status,
            content_hash=content_hash,
            content_type=result.content_type,
            byte_size=byte_size,
            blob_key=blob_key,
            fetcher=result.fetcher,
            etag=etag_store,
            last_modified=last_modified_store,
        )
        .returning(snapshot.c.id)
    ).scalar_one()
    conn.commit()

    content_unchanged = result.http_status == 304 or (
        prior_any is not None and prior_any.content_hash == content_hash
    )
    unchanged = (
        content_unchanged
        and trigger not in _EXCLUDED_TRIGGERS
        and _current_record_pack(conn, entity, url) == site_pack_id
    )

    return SnapshotResult(
        snapshot_id=snapshot_id,
        blob_key=blob_key,
        unchanged=unchanged,
        body=body,
        http_status=result.http_status,
        outcome=result.outcome,
    )
