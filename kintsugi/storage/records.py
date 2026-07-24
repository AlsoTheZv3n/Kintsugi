"""SCD-Typ-2-Record-Writer: geschrieben wird nur bei tatsaechlicher Aenderung.

docs/03-data-model.md §Silver und docs/09-decisions.md ADR-007 (SCD Typ 2 statt
Ueberschreiben). Je Natural Key in einem Lauf:

- keine aktuelle Zeile (``valid_to IS NULL``) -> INSERT, ``inserted``;
- aktuelle Zeile, ``payload_hash`` gleich -> keine neue Zeile, nur ``last_seen_at``
  und ``last_seen_run_id`` fortschreiben, ``unchanged``;
- aktuelle Zeile, ``payload_hash`` verschieden -> die alte Zeile
  ``SET valid_to = :valid_from``, die neue einfuegen, ``versioned``.

``valid_from`` ist **ein** laufweiter, vom Runner gereichter UTC-Zeitstempel — nie
ein zeilenweises ``now()``. So gilt ``old.valid_to == new.valid_from`` exakt: die
Historie hat weder Luecken noch Ueberlappung, eine Zeitreise-Abfrage liefert zu
jedem Zeitpunkt genau eine Zeile je Key. Die Invariante wird nach dem Schreiben
DB-gestuetzt geprueft.

**Duplikate im selben Lauf:** das erste Vorkommen in Discovery-Reihenfolge
gewinnt, spaetere werden verworfen und in ``duplicates`` gezaehlt (speist
``max_duplicate_rate``, docs/02). Deterministisch, nicht dem Zufall der
Einfuegereihenfolge ueberlassen — die Reihenfolge von ``rows`` ist die stabile
Discovery-Reihenfolge (I0.9.4/I0.9.5).

Der partielle Unique-Index ``record_current`` bleibt die durchsetzende Instanz.
Der Writer faengt einen ``IntegrityError`` darauf **nicht** ab — eine zweite
aktuelle Zeile je Key ist ein Fehler, der laut werden muss.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Row, insert, select, update

from kintsugi.canonical import payload_hash
from kintsugi.storage.tables import record

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy import Connection

__all__ = ["RecordRow", "WriteCounters", "touch_last_seen", "write_records"]


@dataclass(frozen=True, slots=True)
class RecordRow:
    """Eine extrahierte, validierte Zeile, bereit fuer den Silver-Bestand."""

    natural_key: str
    payload: dict[str, object]
    snapshot_id: UUID
    quality: dict[str, object] | None = None


@dataclass
class WriteCounters:
    """Zaehler eines write_records-Aufrufs. Ihre Summe ist die Zahl der Eingabezeilen."""

    inserted: int = 0
    versioned: int = 0
    unchanged: int = 0
    duplicates: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.versioned + self.unchanged + self.duplicates


def _current_row(conn: Connection, entity: str, natural_key: str) -> Row[Any] | None:
    return conn.execute(
        select(record.c.id, record.c.payload_hash).where(
            record.c.entity == entity,
            record.c.natural_key == natural_key,
            record.c.valid_to.is_(None),
        )
    ).first()


def _insert_version(
    conn: Connection,
    *,
    entity: str,
    row: RecordRow,
    site_pack_id: UUID,
    payload_h: bytes,
    valid_from: datetime,
    run_id: UUID,
) -> datetime:
    values: dict[str, object] = {
        "entity": entity,
        "natural_key": row.natural_key,
        "snapshot_id": row.snapshot_id,
        "site_pack_id": site_pack_id,
        "payload": row.payload,
        "payload_hash": payload_h,
        "valid_from": valid_from,
        "last_seen_at": valid_from,
        "last_seen_run_id": run_id,
    }
    if row.quality is not None:
        values["quality"] = row.quality
    inserted_valid_from: datetime = conn.execute(
        insert(record).values(**values).returning(record.c.valid_from)
    ).scalar_one()
    return inserted_valid_from


def write_records(
    conn: Connection,
    *,
    entity: str,
    rows: Iterable[RecordRow],
    site_pack_id: UUID,
    run_id: UUID,
    valid_from: datetime,
) -> WriteCounters:
    counters = WriteCounters()
    seen: set[str] = set()
    for row in rows:
        if row.natural_key in seen:
            counters.duplicates += 1
            continue
        seen.add(row.natural_key)

        payload_h = payload_hash(row.payload)
        current = _current_row(conn, entity, row.natural_key)

        if current is None:
            _insert_version(
                conn,
                entity=entity,
                row=row,
                site_pack_id=site_pack_id,
                payload_h=payload_h,
                valid_from=valid_from,
                run_id=run_id,
            )
            counters.inserted += 1
        elif bytes(current.payload_hash) == payload_h:
            # Unveraendert: nur die Sichtung fortschreiben, keine neue Zeile.
            conn.execute(
                update(record)
                .where(record.c.id == current.id)
                .values(last_seen_at=valid_from, last_seen_run_id=run_id)
            )
            counters.unchanged += 1
        else:
            closed_valid_to = conn.execute(
                update(record)
                .where(record.c.id == current.id)
                .values(valid_to=valid_from)
                .returning(record.c.valid_to)
            ).scalar_one()
            new_valid_from = _insert_version(
                conn,
                entity=entity,
                row=row,
                site_pack_id=site_pack_id,
                payload_h=payload_h,
                valid_from=valid_from,
                run_id=run_id,
            )
            # SCD-2-Invariante: die alte Zeile schliesst genau dort, wo die neue
            # beginnt — sonst haette die Historie eine Luecke oder Ueberlappung.
            assert closed_valid_to == new_valid_from, (
                "SCD-2-Invariante verletzt: old.valid_to != new.valid_from"
            )
            counters.versioned += 1

    return counters


def touch_last_seen(
    conn: Connection,
    *,
    entity: str,
    natural_keys: Iterable[str],
    run_id: UUID,
    seen_at: datetime,
) -> int:
    """Schreibt ``last_seen`` fuer bereits aktuelle Keys fort, ohne neue Zeile.

    Der Runner ruft das fuer Seiten, die der versionsbewusste unchanged/304-
    Kurzschluss uebersprungen hat (I0.9.2): sie wurden gesehen, aber nicht neu
    extrahiert. Gibt die Zahl fortgeschriebener Zeilen zurueck.
    """
    keys = list(natural_keys)
    if not keys:
        return 0
    result = conn.execute(
        update(record)
        .where(
            record.c.entity == entity,
            record.c.natural_key.in_(keys),
            record.c.valid_to.is_(None),
        )
        .values(last_seen_at=seen_at, last_seen_run_id=run_id)
    )
    return result.rowcount
