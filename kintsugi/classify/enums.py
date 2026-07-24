"""Drei orthogonale Outcome-Enums (ADR-012).

docs/03-data-model.md §DDL (``run_status``, ``incident_kind``) und README §Five
outcomes. Bewusst **kein** ``StrEnum`` und **kein** ``(str, Enum)``: die Konzepte
duerfen nie zu Strings werden oder gegen sie gleich sein (``PrecheckVerdict.ok ==
"ok"`` ist ``False``). Serialisierung geht ausschliesslich am Speicherrand ueber
``.value``.

- ``PrecheckVerdict`` — die fuenf Ausschlussgruende der docs/04-Vorpruefung plus
  ``ok``.
- ``HarnessOutcome`` — der Ausgang des Harness: ``no_action`` | ``escalated`` |
  ``auto_healed``.
- ``IncidentKind`` — spiegelt den Postgres-Typ ``incident_kind`` (13 Werte, in
  Migration 0001 angelegt); hier **nicht** neu definiert, nur als Python-Enum
  gespiegelt.
- ``run_status`` wird **nicht** neu definiert — die Werte des Phase-0-Migrations-
  Enums stehen als ``RUN_STATUS_VALUES`` zum Abgleich bereit.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "ESCALATE_ON_TO_INCIDENT_KIND",
    "RUN_STATUS_VALUES",
    "HarnessOutcome",
    "IncidentKind",
    "PrecheckVerdict",
]

# Spiegelt run_status aus Migration 0001 (nicht neu definiert).
RUN_STATUS_VALUES = ("running", "ok", "degraded", "failed")


class PrecheckVerdict(Enum):
    """Ausgang der Vorpruefung (docs/04): darf ueberhaupt geheilt werden?"""

    ok = "ok"
    unreachable = "unreachable"
    blocked = "blocked"
    rate_limited = "rate_limited"
    soft_404 = "soft_404"
    quota_exhausted = "quota_exhausted"


class HarnessOutcome(Enum):
    """Ausgang des Klassifikators."""

    no_action = "no_action"
    escalated = "escalated"
    auto_healed = "auto_healed"


class IncidentKind(Enum):
    """Spiegel des Postgres-``incident_kind`` (13 Werte, Migration 0001)."""

    fill_rate_drop = "fill_rate_drop"
    row_count_anomaly = "row_count_anomaly"
    range_violation = "range_violation"
    schema_change = "schema_change"
    field_removed = "field_removed"
    unreachable = "unreachable"
    blocked = "blocked"
    rate_limited = "rate_limited"
    healer_exhausted = "healer_exhausted"
    soft_404 = "soft_404"
    natural_key_broken = "natural_key_broken"
    enum_violation = "enum_violation"
    duplicate_rate_anomaly = "duplicate_rate_anomaly"


# Injektiv: jedes escalate_on-Token auf genau einen incident_kind — Identitaet,
# nie ein Kollaps auf schema_change (die docs/06-TTR-Histogramm ist nach kind
# gelabelt und wuerde zwei Bruchbilder sonst verschmelzen).
ESCALATE_ON_TO_INCIDENT_KIND: dict[str, IncidentKind] = {
    "field_removed": IncidentKind.field_removed,
    "schema_change": IncidentKind.schema_change,
    "enum_violation": IncidentKind.enum_violation,
    "natural_key_broken": IncidentKind.natural_key_broken,
}
