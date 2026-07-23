"""Alembic-Umgebung.

Die URL kommt aus dem Settings-Modul, nicht aus alembic.ini — so bewegt
``KINTSUGI_PG_PORT`` auch die Migrationen und kein Verbindungsstring liegt im
Repo. ``target_metadata`` ist die eine MetaData aus ``kintsugi/storage/tables``.

**Reichweite des Drift-Waechters.** ``compare_type=True`` faengt Typaenderungen.
``compare_server_default=False`` ist Absicht: SQLAlchemy rendert Server-Defaults
und Postgres reflektiert sie in verschiedener Textform (``gen_random_uuid()``
vs. Reflektion, ``'{}'::jsonb``), ein Vergleich waere dauerhaft rot ohne echten
Befund. **CHECK-Constraints liegen ausserhalb der Reichweite** — Autogenerate
vergleicht sie nicht; sie sind durch die je-Migration-Verletzungstests gedeckt.
Ein Waechter, dessen Reichweite ueberzeichnet ist, ist schlechter als einer, der
schmal und ehrlich ist.
"""

from __future__ import annotations

from alembic import context
from kintsugi.config import get_settings
from kintsugi.storage.tables import metadata
from migrations.include import include_object as _include_object
from sqlalchemy import engine_from_config, pool

config = context.config

target_metadata = metadata


def _database_url() -> str:
    return get_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Rendert SQL ohne Verbindung — braucht keine erreichbare Datenbank."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=False,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=False,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
