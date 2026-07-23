"""0004: record, record_current und die gold_book-View

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-23

record_current ist der partielle Unique-Index (entity, natural_key WHERE
valid_to IS NULL) — die Datenbank-Garantie fuer "zweiter Lauf schreibt keine
Duplikate" (Phase-0-DoD). gold_book ist eine View, keine Tabelle, und liegt
damit ausserhalb des tabellenbasierten Drift-Waechters; sie wird per SQL
angelegt und beim Downgrade zuerst wieder verworfen.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from kintsugi.storage.tables import record

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GOLD_BOOK = """
CREATE VIEW gold_book AS
SELECT
    r.natural_key                       AS upc,
    r.payload ->> 'title'               AS title,
    (r.payload ->> 'price')::numeric    AS price,
    r.payload ->> 'currency'            AS currency,
    (r.payload ->> 'availability')::int AS availability,
    r.valid_from                        AS updated_at,
    sp.domain                           AS source_domain,
    sp.version                          AS extractor_version
FROM record r
JOIN site_pack sp ON sp.id = r.site_pack_id
WHERE r.entity = 'book' AND r.valid_to IS NULL
"""


def upgrade() -> None:
    record.create(op.get_bind())
    op.execute(GOLD_BOOK)


def downgrade() -> None:
    op.execute("DROP VIEW gold_book")
    record.drop(op.get_bind())
