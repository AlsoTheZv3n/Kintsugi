"""0001: die sechs ENUM-Typen plus die ergaenzten incident_kind-Werte

Revision ID: 0001
Revises:
Create Date: 2026-07-23

docs/03-data-model.md nennt neun incident_kind-Werte. Ergaenzt sind vier, die
der Outcome-Klassifikator (Phase 1) und der Mutationskatalog brauchen:
soft_404 (N02), natural_key_broken (M15), enum_violation (M16) und
duplicate_rate_anomaly (Duplikatrate ueber max_duplicate_rate, docs/02
§quality). Dies ist die einzige Stelle, an der diese Labels entstehen; keine
spaetere Revision fuegt sie erneut hinzu. Die Typen werden hier angelegt
(tables.py deklariert sie mit create_type=False), sodass die
CREATE-TABLE-Migrationen sie nur referenzieren.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS: dict[str, tuple[str, ...]] = {
    "site_pack_status": ("draft", "canary", "active", "retired", "rejected"),
    "run_trigger": ("schedule", "manual", "canary", "replay"),
    "run_status": ("running", "ok", "degraded", "failed"),
    "incident_severity": ("info", "warn", "critical"),
    "incident_kind": (
        "fill_rate_drop",
        "row_count_anomaly",
        "range_violation",
        "schema_change",
        "field_removed",
        "unreachable",
        "blocked",
        "rate_limited",
        "healer_exhausted",
        "soft_404",
        "natural_key_broken",
        "enum_violation",
        "duplicate_rate_anomaly",
    ),
    "incident_resolution": (
        "auto_healed",
        "human_fixed",
        "schema_migrated",
        "false_positive",
        "source_recovered",
    ),
}


def upgrade() -> None:
    for name, values in ENUMS.items():
        rendered = ", ".join(f"'{v}'" for v in values)
        op.execute(f"CREATE TYPE {name} AS ENUM ({rendered})")


def downgrade() -> None:
    for name in reversed(list(ENUMS)):
        op.execute(f"DROP TYPE {name}")
