"""Synchrone SQLAlchemy-Engine und Transaktionshelfer.

ADR-008: SQLAlchemy 2.0 **Core** auf dem synchronen **psycopg3**-Treiber
(`postgresql+psycopg://`), keine ORM-Session-Schicht, kein asyncpg. Phase 0 und 1
sind bewusst synchron; async kommt erst mit der API in Phase 3.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text

from kintsugi.config import Settings, get_settings

# Wird zur Laufzeit geprueft: der Treiber muss der synchrone psycopg3 sein.
SYNC_DRIVER_PREFIX = "postgresql+psycopg://"

# Zwischengespeichert je aufgeloester URL — nicht je Settings-Objekt, denn
# pydantic-Modelle sind nicht hashbar und taugen nicht als lru_cache-Schluessel.
_ENGINES: dict[str, Engine] = {}


def get_engine(settings: Settings | None = None) -> Engine:
    """Engine, zwischengespeichert je Datenbank-URL.

    ``pool_pre_ping=True`` verwirft tote Verbindungen aus dem Pool, bevor sie
    eine Query mit einem Verbindungsfehler quittieren — im Container-Betrieb
    kann Postgres neu starten, waehrend der Pool noch alte Handles haelt.
    """
    settings = settings or get_settings()
    url = settings.database_url.get_secret_value()
    if not url.startswith(SYNC_DRIVER_PREFIX):
        raise ValueError(
            f"database_url muss den synchronen psycopg3-Treiber nutzen "
            f"({SYNC_DRIVER_PREFIX}...), nicht {url.split('://', 1)[0]}://. ADR-008."
        )

    if url not in _ENGINES:
        # Zeitzone als libpq-Startparameter, nicht per SET in einem
        # connect-Event: der Startparameter gilt fuer die ganze Sitzung und
        # ueberlebt den Pool-Reset beim Zurueckgeben einer Verbindung, waehrend
        # ein einmaliges SET beim Roh-Connect das nicht zuverlaessig tut.
        # timestamptz round-trippt so auf der Windows-Maschine und dem
        # Linux-CI-Runner identisch.
        engine = create_engine(
            url,
            pool_pre_ping=True,
            future=True,
            connect_args={"options": "-c timezone=UTC"},
        )
        _ENGINES[url] = engine

    return _ENGINES[url]


def reset_engines() -> None:
    """Verwirft die zwischengespeicherten Engines (fuer Tests)."""
    for engine in _ENGINES.values():
        engine.dispose()
    _ENGINES.clear()


@contextmanager
def transaction(engine: Engine | None = None) -> Iterator[Connection]:
    """Transaktionsklammer: sauberer Austritt committet, eine Ausnahme rollt zurueck."""
    engine = engine or get_engine()
    with engine.begin() as connection:
        yield connection


def current_timezone(connection: Connection) -> str:
    """Zeitzone der Sitzung — fuer den Test, der UTC belegt."""
    result = connection.execute(text("SELECT current_setting('TimeZone')"))
    return str(result.scalar_one())
