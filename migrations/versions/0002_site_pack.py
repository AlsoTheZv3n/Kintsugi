"""0002: site_pack mit partiellen Unique-Indizes und CHECKs

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-23

Baut die Tabelle aus dem eingefrorenen Table-Objekt in kintsugi/storage/tables,
damit Constraint- und Indexnamen exakt der naming_convention entsprechen und
der Autogenerate-Drift am head leer bleibt. Die beiden partiellen Unique-Indizes
site_pack_one_active und site_pack_one_canary erzwingen in der Datenbank, dass
je (domain, entity) hoechstens eine aktive und eine Canary-Version existiert.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from kintsugi.storage.tables import site_pack

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    site_pack.create(op.get_bind())


def downgrade() -> None:
    site_pack.drop(op.get_bind())
