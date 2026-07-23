"""0003: run und snapshot mit Lifecycle- und Validator-CHECKs

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-23

run traegt die Lifecycle-CHECKs (finished_at >= started_at, nicht-negative
Zaehler), snapshot die Validator-CHECKs (http_status 100..599, byte_size >= 0,
content_hash genau 32 sha256-Rohbytes). CHECKs liegen ausserhalb des
Drift-Waechters und sind durch die Verletzungstests dieser Migration gedeckt.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from kintsugi.storage.tables import run, snapshot

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    run.create(bind)
    snapshot.create(bind)


def downgrade() -> None:
    bind = op.get_bind()
    snapshot.drop(bind)
    run.drop(bind)
