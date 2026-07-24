r"""0006: gold_book mit Quarantaene-Praedikat und cast-sicheren Guards

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-24

Ersetzt die gold_book-View aus 0004. Zwei Abweichungen von der Definition in
docs/03 §Gold, begruendet durch docs/06 §Betriebsziele („stille Datenfehler,
die die API erreichen: 0"):

1. Zeilen mit Eintraegen unter ``quality->'violations'`` (Bereichs- oder
   Enum-Verletzer, in Silver als Evidenz behalten) werden ausgeschlossen.
2. Die Casts ``(payload->>'price')::numeric`` und ``::int`` sind mit einer
   Regex-CASE gekapselt. Ein einziger fehlerhafter Payload (direkt per SQL
   eingefuegt, ohne Violations-Marke) machte sonst aus einem Zeilendefekt einen
   Full-Table-SELECT-Fehler — ein totaler API-Ausfall.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GUARDED = r"""
CREATE VIEW gold_book AS
SELECT
    r.natural_key                       AS upc,
    r.payload ->> 'title'               AS title,
    CASE WHEN r.payload ->> 'price' ~ '^-?\d+(\.\d+)?$'
         THEN (r.payload ->> 'price')::numeric END AS price,
    r.payload ->> 'currency'            AS currency,
    CASE WHEN r.payload ->> 'availability' ~ '^-?\d+$'
         THEN (r.payload ->> 'availability')::int END AS availability,
    r.valid_from                        AS updated_at,
    sp.domain                           AS source_domain,
    sp.version                          AS extractor_version
FROM record r
JOIN site_pack sp ON sp.id = r.site_pack_id
WHERE r.entity = 'book' AND r.valid_to IS NULL
  AND (r.quality -> 'violations') IS NULL
"""

# Die urspruengliche View aus 0004 (docs/03 §Gold) fuer das Downgrade.
ORIGINAL = """
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
    op.execute("DROP VIEW gold_book")
    op.execute(GUARDED)


def downgrade() -> None:
    op.execute("DROP VIEW gold_book")
    op.execute(ORIGINAL)
