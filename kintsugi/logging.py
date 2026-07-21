"""Strukturierte Protokollierung.

`docs/06-observability.md` fuehrt die Stufe ``info`` — darunter „automatisch
geheilt, Canary bestanden" — ausdruecklich als **nur Log und Dashboard**. Fuer
diese Stufe ist das Protokoll damit die einzige Beweisspur, die es gibt.

Deshalb ist `bind_run_context` keine Bequemlichkeit: erst die vier gebundenen
Schluessel machen eine Logzeile mit ``run.id``, ``site_pack.version`` und dem
Prometheus-Labelsatz ``{domain,entity}`` aus demselben Dokument verknuepfbar.
Ohne sie hat ein automatisch geheilter Lauf ab Phase 2 keinen Nachweis.

Der Renderer wird an der Konsole entschieden, nicht ueber einen zweiten
Schalter: am Terminal farbig lesbar, sonst maschinenlesbares JSON.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import structlog

__all__ = ["bind_run_context", "configure_logging", "get_logger"]

_LEVELS = {"CRITICAL": 50, "ERROR": 40, "WARNING": 30, "INFO": 20, "DEBUG": 10}


class _StderrLogger:
    """Schreibt nach ``sys.stderr``, aufgeloest zur Schreibzeit.

    structlogs ``WriteLogger`` bindet den Strom bei der Konstruktion fest. Das
    bricht ueberall dort, wo ``sys.stderr`` spaeter ausgetauscht wird — in
    Tests durch capsys, im Betrieb durch jede Umleitung. Die spaete Aufloesung
    kostet nichts und macht den Logger gegen beides unempfindlich.
    """

    def msg(self, message: str) -> None:
        stream = sys.stderr
        stream.write(message + "\n")
        stream.flush()

    log = debug = info = warning = warn = error = critical = exception = fatal = msg


class _StderrLoggerFactory:
    """Liefert ``_StderrLogger``; Signatur wie von structlog erwartet.

    structlog reicht der Fabrik den Loggernamen und weitere Positionsargumente
    durch. Dieser Logger braucht keines davon, muss sie aber entgegennehmen.
    """

    def __call__(self, *args: object) -> _StderrLogger:
        return _StderrLogger()


def configure_logging(level: str | None = None) -> None:
    """Richtet structlog ein. Ohne Argument gilt ``log_level`` aus den Settings."""
    if level is None:
        from kintsugi.config import get_settings

        level = get_settings().log_level

    threshold = _LEVELS.get(level.upper(), 20)

    renderer: Any
    if sys.stderr.isatty():
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            # UTC und ISO-8601, damit Logzeilen mit run.started_at timestamptz
            # aus docs/03-data-model.md ohne Umrechnung zusammenpassen.
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(threshold),
        logger_factory=_StderrLoggerFactory(),
        cache_logger_on_first_use=False,
    )


@contextmanager
def bind_run_context(
    run_id: UUID,
    domain: str,
    entity: str,
    site_pack_version: int,
) -> Iterator[None]:
    """Bindet die vier Schluessel an jede Logzeile innerhalb des Laufs.

    Beim Verlassen werden sie wieder geloest, damit Zeilen ausserhalb des Laufs
    nicht faelschlich einem Lauf zugerechnet werden.
    """
    keys = ("run_id", "domain", "entity", "site_pack_version")
    structlog.contextvars.bind_contextvars(
        run_id=str(run_id),
        domain=domain,
        entity=entity,
        site_pack_version=site_pack_version,
    )
    try:
        yield
    finally:
        structlog.contextvars.unbind_contextvars(*keys)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Einziger Bezugsweg fuer Logger; kein Modul importiert structlog selbst."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
