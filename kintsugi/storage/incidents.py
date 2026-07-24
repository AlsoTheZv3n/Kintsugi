"""Incident-Writer: genau ein offener Incident je (site_pack, kind, field) (I1.4.5).

docs/03-data-model.md §Incidents, docs/06-observability.md §Alarmstufen. Auf
SQLAlchemy 2.0 Core ueber den synchronen psycopg3-Treiber — ADR-008 sperrt den
async-Treiber bis einschliesslich Phase 1, und dieser Pfad liegt im Guard-Scan.

``report`` schreibt mit ``INSERT ... ON CONFLICT DO UPDATE`` gegen den partiellen
Unique-Index ``incident_open_dedup`` (offene Incidents, ``NULLS NOT DISTINCT``,
damit auch zwei ``field IS NULL``-Incidents kollidieren). Eine wiederholte Meldung
haelt so **genau eine** offene Zeile, frischt die Evidence auf und zaehlt
``evidence->>'occurrences'`` hoch (docs/06 §Daempfung: „wiederholte Meldungen
desselben Incidents unterdrueckt").

``report`` committet **nicht** — der Lauf-Abschluss (Runner, #99) schreibt
Incident und ``run.metrics`` in *einer* Transaktion; ein Fehler dazwischen rollt
beide zurueck.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from kintsugi.storage.tables import incident

if TYPE_CHECKING:
    from sqlalchemy import Connection

    from kintsugi.classify.enums import IncidentKind
    from kintsugi.classify.outcome import Severity

__all__ = ["report"]

# Evidence-Merge im Konflikt: die bestehende Evidence mit der neuen ueberlagern
# (die letzte Meldung gewinnt fuer snapshot_id etc.), dann ``occurrences`` aus der
# ALTEN Zeile plus eins draufsetzen. Die Reihenfolge der ``||``-Operatoren
# entscheidet — der letzte Term gewinnt, deshalb steht occurrences zuletzt.
_MERGE_EVIDENCE = text(
    "(incident.evidence || excluded.evidence) || "
    "jsonb_build_object('occurrences', "
    "(COALESCE((incident.evidence->>'occurrences')::int, 0) + 1))"
)


def report(
    conn: Connection,
    *,
    site_pack_id: UUID,
    run_id: UUID | None,
    kind: IncidentKind,
    severity: Severity,
    field: str | None,
    evidence: dict[str, object],
) -> UUID:
    """Oeffnet einen Incident oder frischt den bestehenden offenen auf; gibt die id."""
    insert_stmt = pg_insert(incident).values(
        site_pack_id=site_pack_id,
        run_id=run_id,
        kind=kind.value,
        severity=severity,
        field=field,
        evidence={**evidence, "occurrences": 1},
    )
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=[incident.c.site_pack_id, incident.c.kind, incident.c.field],
        index_where=text("closed_at IS NULL"),
        set_={
            "run_id": insert_stmt.excluded.run_id,
            "severity": insert_stmt.excluded.severity,
            "evidence": _MERGE_EVIDENCE,
        },
    ).returning(incident.c.id)
    incident_id: UUID = conn.execute(stmt).scalar_one()
    return incident_id
