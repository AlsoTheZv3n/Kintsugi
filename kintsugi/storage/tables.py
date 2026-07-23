"""Das vollstaendige Silver/Bronze-Schema als SQLAlchemy-Core-Metadata.

**Nur Core**: kein ORM-Deklarativmodell, keine Session (ADR-008). Diese Datei
ist die einzige Quelle der Wahrheit fuer das Schema; ``migrations/env.py``
importiert ``metadata`` als ``target_metadata``, und die Migrationen 0001 bis
0005 bauen die Datenbank exakt hierhin auf (jede via ``Table.create``). Am head
ist der Autogenerate-Diff leer.

DDL-Vorlage: docs/03-data-model.md, ergaenzt um die CHECK- und Spalten-
Anforderungen der Migrations-Issues. Die ``naming_convention`` ist nicht
kosmetisch: ohne benannte Constraints meldet Alembic-Autogenerate dauerhaft
Drift auf unbenannten Constraints und Fremdschluesseln.
"""

from __future__ import annotations

import uuid
from datetime import datetime

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


def _uuid_pk() -> Column[uuid.UUID]:
    return Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


def _tstz(name: str, *, nullable: bool = True, default_now: bool = False) -> Column[datetime]:
    return Column(
        name,
        postgresql.TIMESTAMP(timezone=True),
        nullable=nullable,
        server_default=text("now()") if default_now else None,
    )


# --------------------------------------------------------------------------
# ENUM-Typen. create_type=False: die Migration 0001 legt den Typ an.
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
# docs/03 nennt neun Arten. Ergaenzt sind vier, die der Outcome-Klassifikator
# (Phase 1) und der Mutationskatalog brauchen: soft_404 (N02),
# natural_key_broken (M15), enum_violation (M16) und duplicate_rate_anomaly
# (Duplikatrate ueber max_duplicate_rate). 13 Werte insgesamt.
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
    "duplicate_rate_anomaly",
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
    _uuid_pk(),
    Column("domain", Text, nullable=False),
    Column("entity", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", SITE_PACK_STATUS, nullable=False, server_default=text("'draft'")),
    Column("spec", postgresql.JSONB, nullable=False),
    Column("parent_version", Integer),
    _tstz("created_at", nullable=False, default_now=True),
    Column("created_by", Text, nullable=False),
    _tstz("activated_at"),
    _tstz("retired_at"),
    Column("notes", Text),
    UniqueConstraint("domain", "entity", "version", name="uq_site_pack_domain_entity_version"),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint(
        "parent_version IS NULL OR parent_version < version", name="parent_before_self"
    ),
    # Der spec-Rumpf muss zur Identitaetsspalte passen; sonst zeigt ein Pack auf
    # eine andere Domain als die, unter der es gefuehrt wird.
    CheckConstraint("spec ->> 'domain' = domain", name="spec_domain_matches"),
    # created_by ist 'human:<name>' oder 'healer:<version>' — die einzige
    # belastbare Auswertung "wie viele aktive Versionen stammen von der Maschine"
    # (docs/02 §Lebenszyklus) braucht dieses Namensschema.
    CheckConstraint("created_by ~ '^(human|healer):.+'", name="created_by_namespaced"),
    # Eine aktive Version traegt einen Aktivierungszeitpunkt.
    CheckConstraint(
        "status <> 'active' OR activated_at IS NOT NULL", name="active_needs_activation"
    ),
)
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
    _uuid_pk(),
    Column(
        "site_pack_id", postgresql.UUID(as_uuid=True), ForeignKey("site_pack.id"), nullable=False
    ),
    Column("trigger", RUN_TRIGGER, nullable=False),
    Column("status", RUN_STATUS, nullable=False, server_default=text("'running'")),
    _tstz("started_at", nullable=False, default_now=True),
    _tstz("finished_at"),
    Column("pages_fetched", Integer, nullable=False, server_default=text("0")),
    Column("rows_extracted", Integer, nullable=False, server_default=text("0")),
    Column("metrics", postgresql.JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("error", Text),
    CheckConstraint(
        "finished_at IS NULL OR finished_at >= started_at", name="finished_after_started"
    ),
    # Ein terminaler Status (ok/degraded/failed) traegt einen Abschlusszeitpunkt;
    # nur 'running' darf offen sein.
    CheckConstraint(
        "status = 'running' OR finished_at IS NOT NULL", name="terminal_needs_finished"
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
    _uuid_pk(),
    Column("run_id", postgresql.UUID(as_uuid=True), ForeignKey("run.id"), nullable=False),
    Column("url", Text, nullable=False),
    _tstz("fetched_at", nullable=False, default_now=True),
    Column("http_status", SmallInteger, nullable=False),
    Column("content_hash", LargeBinary, nullable=False),
    Column("content_type", Text),
    Column("byte_size", Integer, nullable=False),
    Column("blob_key", Text, nullable=False),
    Column("fetcher", Text, nullable=False),
    # ETag und Last-Modified aus der Antwort, damit der naechste Lauf bedingte
    # Anfragen stellen kann (docs/03 §Bronze, conditional_requests).
    Column("etag", Text),
    Column("last_modified", Text),
    Column("is_golden", postgresql.BOOLEAN, nullable=False, server_default=text("false")),
    Column("golden_label", Text),
    CheckConstraint("http_status BETWEEN 100 AND 599", name="http_status_range"),
    CheckConstraint("byte_size >= 0", name="byte_size_nonneg"),
    CheckConstraint("octet_length(content_hash) = 32", name="content_hash_sha256"),
    # Nur die beiden implementierten Fetcher (ADR-008: httpx jetzt, playwright
    # ab Phase 5). 'curl' o. Ae. ist ein Tippfehler, kein gueltiger Wert.
    CheckConstraint("fetcher IN ('httpx', 'playwright')", name="fetcher_known"),
    # Ein Golden-Snapshot traegt ein Label, sonst ist der Regressionsbestand
    # nicht zuzuordnen.
    CheckConstraint("NOT is_golden OR golden_label IS NOT NULL", name="golden_needs_label"),
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
    _uuid_pk(),
    Column("entity", Text, nullable=False),
    Column("natural_key", Text, nullable=False),
    Column("snapshot_id", postgresql.UUID(as_uuid=True), ForeignKey("snapshot.id"), nullable=False),
    Column(
        "site_pack_id", postgresql.UUID(as_uuid=True), ForeignKey("site_pack.id"), nullable=False
    ),
    _tstz("valid_from", nullable=False, default_now=True),
    _tstz("valid_to"),
    Column("payload", postgresql.JSONB, nullable=False),
    Column("payload_hash", LargeBinary, nullable=False),
    Column("quality", postgresql.JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    # Wann diese aktuell gueltige Zeile zuletzt unveraendert bestaetigt wurde,
    # und in welchem Lauf. Erlaubt "seit N Tagen nicht mehr gesehen", ohne eine
    # neue SCD-2-Zeile zu schreiben (docs/03 §Silver, payload_hash gleich ->
    # nur valid_from bestaetigen).
    _tstz("last_seen_at"),
    Column("last_seen_run_id", postgresql.UUID(as_uuid=True), ForeignKey("run.id")),
    CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_interval"),
    CheckConstraint("octet_length(payload_hash) = 32", name="payload_hash_sha256"),
)
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
Index("record_changes", record.c.entity, record.c.valid_from.desc(), record.c.id)
Index("record_site_pack", record.c.site_pack_id)
Index("record_snapshot", record.c.snapshot_id)

# --------------------------------------------------------------------------
# incident (Migration 0005) — hier angelegt, aber erst ab Phase 1 geschrieben.
# --------------------------------------------------------------------------

incident = Table(
    "incident",
    metadata,
    _uuid_pk(),
    Column(
        "site_pack_id", postgresql.UUID(as_uuid=True), ForeignKey("site_pack.id"), nullable=False
    ),
    Column("run_id", postgresql.UUID(as_uuid=True), ForeignKey("run.id")),
    Column("kind", INCIDENT_KIND, nullable=False),
    Column("severity", INCIDENT_SEVERITY, nullable=False),
    Column("field", Text),
    _tstz("opened_at", nullable=False, default_now=True),
    _tstz("closed_at"),
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
# Genau ein offener Incident je (site_pack, kind, field). NULLS NOT DISTINCT,
# damit zwei offene Incidents mit field IS NULL ebenfalls kollidieren — sonst
# umginge ein NULL-Feld die Deduplizierung.
Index(
    "incident_open_dedup",
    incident.c.site_pack_id,
    incident.c.kind,
    incident.c.field,
    unique=True,
    postgresql_where=text("closed_at IS NULL"),
    postgresql_nulls_not_distinct=True,
)
Index(
    "incident_open",
    incident.c.severity,
    incident.c.opened_at.desc(),
    postgresql_where=text("closed_at IS NULL"),
)

__all__ = ["ALL_ENUMS", "incident", "metadata", "record", "run", "site_pack", "snapshot"]
