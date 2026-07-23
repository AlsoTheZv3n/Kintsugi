"""0005: incident mit Open-Dedup-Index und Schliessungs-CHECK

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-23

Die incident-Tabelle entsteht bereits in Phase 0, obwohl sie erst ab Phase 1
geschrieben wird. Der partielle Index incident_open (severity, opened_at DESC
WHERE closed_at IS NULL) traegt die Abfrage nach offenen Vorfaellen. Der
Schliessungs-CHECK erzwingt: ein geschlossener Incident traegt eine Aufloesung,
ein offener nicht.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from kintsugi.storage.tables import incident

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    incident.create(op.get_bind())


def downgrade() -> None:
    incident.drop(op.get_bind())
