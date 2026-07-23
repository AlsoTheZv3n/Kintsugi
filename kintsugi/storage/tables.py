"""Das vollstaendige Silver/Bronze-Schema als SQLAlchemy-Core-Metadata.

**Nur Core**: kein ORM-Deklarativmodell, keine Session (ADR-008). Diese Datei
ist die einzige Quelle der Wahrheit fuer das Schema; ``migrations/env.py``
importiert ``metadata`` als ``target_metadata``, und die Migrationen 0001 bis
0005 bauen die Datenbank exakt hierhin auf. Am head ist der Autogenerate-Diff
leer.

DDL-Vorlage: docs/03-data-model.md. Die ``naming_convention`` ist nicht
kosmetisch — ohne benannte Constraints meldet Alembic-Autogenerate dauerhaft
Drift auf unbenannten Indizes und Fremdschluesseln.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

# --------------------------------------------------------------------------
# ENUM-Typen. create_type=False: die Migration 0001 legt den Typ an, nicht das
# beilaeufige Erzeugen beim ersten CREATE TABLE.
# --------------------------------------------------------------------------

SITE_PACK_STATUS = postgresql.ENUM(
    "draft",
    "canary",
    "active",
    "retired",
    "rejected",
    name="site_pack_status",
    create_type=False,
)
RUN_TRIGGER = postgresql.ENUM(
    "schedule",
    "manual",
    "canary",
    "replay",
    name="run_trigger",
    create_type=False,
)
RUN_STATUS = postgresql.ENUM(
    "running",
    "ok",
    "degraded",
    "failed",
    name="run_status",
    create_type=False,
)
INCIDENT_SEVERITY = postgresql.ENUM(
    "info",
    "warn",
    "critical",
    name="incident_severity",
    create_type=False,
)
# docs/03 nennt neun Arten. Der Outcome-Klassifikator (Phase 1) und der
# Mutationskatalog brauchen drei weitere: soft_404 (N02), natural_key_broken
# (M15) und enum_violation (M16). Sie sind hier ergaenzt.
INCIDENT_KIND = postgresql.ENUM(
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
    name="incident_kind",
    create_type=False,
)
INCIDENT_RESOLUTION = postgresql.ENUM(
    "auto_healed",
    "human_fixed",
    "schema_migrated",
    "false_positive",
    "source_recovered",
    name="incident_resolution",
    create_type=False,
)

ALL_ENUMS = (
    SITE_PACK_STATUS,
    RUN_TRIGGER,
    RUN_STATUS,
    INCIDENT_SEVERITY,
    INCIDENT_KIND,
    INCIDENT_RESOLUTION,
)


# --------------------------------------------------------------------------
# site_pack (Migration 0002)
# --------------------------------------------------------------------------

site_pack = Table(
    "site_pack",
    metadata,
    Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column("domain", Text, nullable=False),
    Column("entity", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", SITE_PACK_STATUS, nullable=False, server_default=text("'draft'")),
    Column("spec", postgresql.JSONB, nullable=False),
    Column("parent_version", Integer),
    Column(
        "created_at",
        postgresql.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    Column("created_by", Text, nullable=False),
    Column("activated_at", postgresql.TIMESTAMP(timezone=True)),
    Column("retired_at", postgresql.TIMESTAMP(timezone=True)),
    Column("notes", Text),
    UniqueConstraint("domain", "entity", "version", name="uq_site_pack_domain_entity_version"),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint(
        "parent_version IS NULL OR parent_version < version", name="parent_before_self"
    ),
)
# Genau eine aktive und genau eine Canary-Version je (domain, entity) — in der
# Datenbank erzwungen, nicht in der Anwendung.
Index(
    "site_pack_one_active",
    site_pack.c.domain,
    site_pack.c.entity,
    unique=True,
    postgresql_where=text("status = 'active'"),
)
Index(
    "site_pack_one_canary",
    site_pack.c.domain,
    site_pack.c.entity,
    unique=True,
    postgresql_where=text("status = 'canary'"),
)

# --------------------------------------------------------------------------
# run (Migration 0003)
# --------------------------------------------------------------------------

run = Table(
    "run",
    metadata,
    Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column(
        "site_pack_id", postgresql.UUID(as_uuid=True), ForeignKey("site_pack.id"), nullable=False
    ),
    Column("trigger", RUN_TRIGGER, nullable=False),
    Column("status", RUN_STATUS, nullable=False, server_default=text("'running'")),
    Column(
        "started_at",
        postgresql.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    Column("finished_at", postgresql.TIMESTAMP(timezone=True)),
    Column("pages_fetched", Integer, nullable=False, server_default=text("0")),
    Column("rows_extracted", Integer, nullable=False, server_default=text("0")),
    Column("metrics", postgresql.JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("error", Text),
    CheckConstraint(
        "finished_at IS NULL OR finished_at >= started_at", name="finished_after_started"
    ),
    CheckConstraint("pages_fetched >= 0", name="pages_nonneg"),
    CheckConstraint("rows_extracted >= 0", name="rows_nonneg"),
)
Index("run_pack_time", run.c.site_pack_id, run.c.started_at.desc())

# --------------------------------------------------------------------------
# snapshot (Migration 0003)
# --------------------------------------------------------------------------

snapshot = Table(
    "snapshot",
    metadata,
    Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column("run_id", postgresql.UUID(as_uuid=True), ForeignKey("run.id"), nullable=False),
    Column("url", Text, nullable=False),
    Column(
        "fetched_at",
        postgresql.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    Column("http_status", SmallInteger, nullable=False),
    Column("content_hash", LargeBinary, nullable=False),
    Column("content_type", Text),
    Column("byte_size", Integer, nullable=False),
    Column("blob_key", Text, nullable=False),
    Column("fetcher", Text, nullable=False),
    Column("is_golden", postgresql.BOOLEAN, nullable=False, server_default=text("false")),
    Column("golden_label", Text),
    CheckConstraint("http_status BETWEEN 100 AND 599", name="http_status_range"),
    CheckConstraint("byte_size >= 0", name="byte_size_nonneg"),
    CheckConstraint("octet_length(content_hash) = 32", name="content_hash_sha256"),
)
Index("snapshot_url_time", snapshot.c.url, snapshot.c.fetched_at.desc())
Index("snapshot_hash", snapshot.c.content_hash)
Index("snapshot_golden", snapshot.c.run_id, postgresql_where=text("is_golden"))

# --------------------------------------------------------------------------
# record (Migration 0004)
# --------------------------------------------------------------------------

record = Table(
    "record",
    metadata,
    Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column("entity", Text, nullable=False),
    Column("natural_key", Text, nullable=False),
    Column("snapshot_id", postgresql.UUID(as_uuid=True), ForeignKey("snapshot.id"), nullable=False),
    Column(
        "site_pack_id", postgresql.UUID(as_uuid=True), ForeignKey("site_pack.id"), nullable=False
    ),
    Column(
        "valid_from",
        postgresql.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    Column("valid_to", postgresql.TIMESTAMP(timezone=True)),
    Column("payload", postgresql.JSONB, nullable=False),
    Column("payload_hash", LargeBinary, nullable=False),
    Column("quality", postgresql.JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_interval"),
    CheckConstraint("octet_length(payload_hash) = 32", name="payload_hash_sha256"),
)
# Genau eine aktuell gueltige Zeile je (entity, natural_key). Das ist die
# Datenbank-Garantie fuer "zweiter Lauf schreibt keine Duplikate" (Phase-0-DoD).
Index(
    "record_current",
    record.c.entity,
    record.c.natural_key,
    unique=True,
    postgresql_where=text("valid_to IS NULL"),
)
Index(
    "record_payload",
    record.c.payload,
    postgresql_using="gin",
    postgresql_ops={"payload": "jsonb_path_ops"},
)
Index("record_changes", record.c.entity, record.c.valid_from.desc())

# --------------------------------------------------------------------------
# incident (Migration 0005) — hier angelegt, aber erst ab Phase 1 geschrieben.
# --------------------------------------------------------------------------

incident = Table(
    "incident",
    metadata,
    Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column(
        "site_pack_id", postgresql.UUID(as_uuid=True), ForeignKey("site_pack.id"), nullable=False
    ),
    Column("run_id", postgresql.UUID(as_uuid=True), ForeignKey("run.id")),
    Column("kind", INCIDENT_KIND, nullable=False),
    Column("severity", INCIDENT_SEVERITY, nullable=False),
    Column("field", Text),
    Column(
        "opened_at",
        postgresql.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    Column("closed_at", postgresql.TIMESTAMP(timezone=True)),
    Column("resolution", INCIDENT_RESOLUTION),
    Column("evidence", postgresql.JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("assignee", Text),
    # Ein geschlossener Incident traegt eine Aufloesung; ein offener nicht.
    CheckConstraint(
        "(closed_at IS NULL AND resolution IS NULL) "
        "OR (closed_at IS NOT NULL AND resolution IS NOT NULL)",
        name="closure_needs_resolution",
    ),
    CheckConstraint("closed_at IS NULL OR closed_at >= opened_at", name="closed_after_opened"),
)
Index(
    "incident_open",
    incident.c.severity,
    incident.c.opened_at.desc(),
    postgresql_where=text("closed_at IS NULL"),
)

__all__ = ["ALL_ENUMS", "incident", "metadata", "record", "run", "site_pack", "snapshot"]
