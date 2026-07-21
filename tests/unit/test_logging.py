"""Prueft kintsugi/logging.py.

Der wichtigste Test ist der auf `bind_run_context`. `docs/06-observability.md`
gibt der Stufe ``info`` als Kanal ausdruecklich „nur Log und Dashboard" — fuer
automatisch geheilte Laeufe ab Phase 2 ist die Logzeile also der einzige
Nachweis. Traegt sie run_id, domain, entity und site_pack_version nicht, ist
sie mit nichts verknuepfbar und der Nachweis wertlos.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import UUID

import pytest
import structlog
from kintsugi.logging import bind_run_context, configure_logging, get_logger

PROJECT_ROOT = Path(__file__).resolve().parents[2]

RUN_ID = UUID("9f87b2c1-0000-4000-8000-000000000001")
RUN_KEYS = ("run_id", "domain", "entity", "site_pack_version")


@pytest.fixture(autouse=True)
def _saubere_konfiguration():
    """structlog ist global; jeder Test bekommt einen unbelasteten Zustand."""
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    yield
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


# --------------------------------------------------------------------------
# Laufkontext
# --------------------------------------------------------------------------


@pytest.fixture
def json_lines(monkeypatch, capsys):
    """Liest Ereignisse ueber die echte Prozessorkette aus.

    `structlog.testing.capture_logs` taugt hier nicht: es ersetzt die
    Prozessorkette durch einen reinen Sammler und laesst damit gerade
    `merge_contextvars` weg — also genau den Schritt, der geprueft werden soll.
    """
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False, raising=False)
    configure_logging("DEBUG")

    def read():
        err = capsys.readouterr().err.strip()
        return [json.loads(line) for line in err.splitlines() if line.strip()]

    return read


def test_laufkontext_haengt_an_jeder_zeile(json_lines):
    with bind_run_context(RUN_ID, "books.toscrape.com", "book", 3):
        get_logger(__name__).info("lauf gestartet")

    events = json_lines()
    assert len(events) == 1
    event = events[0]
    for key in RUN_KEYS:
        assert key in event, f"{key} fehlt — die Zeile ist mit nichts verknuepfbar"
    assert event["run_id"] == str(RUN_ID)
    assert event["domain"] == "books.toscrape.com"
    assert event["entity"] == "book"
    assert event["site_pack_version"] == 3


def test_kontext_wird_beim_verlassen_geloest(json_lines):
    """Sonst wuerden spaetere Zeilen faelschlich einem Lauf zugerechnet."""
    with bind_run_context(RUN_ID, "books.toscrape.com", "book", 3):
        get_logger(__name__).info("drinnen")
    get_logger(__name__).info("draussen")

    drinnen, draussen = json_lines()
    assert all(k in drinnen for k in RUN_KEYS)
    assert not any(k in draussen for k in RUN_KEYS), draussen


def test_kontext_wird_auch_bei_ausnahme_geloest(json_lines):
    with pytest.raises(ValueError, match="geplatzt"), bind_run_context(RUN_ID, "d", "e", 1):
        raise ValueError("geplatzt")

    get_logger(__name__).info("danach")
    # Einmal auslesen: readouterr() leert den Puffer beim ersten Aufruf.
    events = json_lines()
    assert not any(k in events[0] for k in RUN_KEYS)


# --------------------------------------------------------------------------
# Renderer
# --------------------------------------------------------------------------


def test_ohne_tty_wird_json_geschrieben(monkeypatch, capsys):
    """Im Container muss die Zeile maschinenlesbar sein, ohne zweiten Schalter."""
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False, raising=False)
    configure_logging("INFO")
    get_logger(__name__).info("testereignis", feld="wert")

    line = capsys.readouterr().err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "testereignis"
    assert payload["feld"] == "wert"
    assert "timestamp" in payload
    assert payload["level"] == "info"


def test_zeitstempel_ist_utc_nach_iso_8601(monkeypatch, capsys):
    """Muss ohne Umrechnung zu run.started_at timestamptz passen."""
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False, raising=False)
    configure_logging("INFO")
    get_logger(__name__).info("zeitprobe")

    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["timestamp"].endswith("Z"), payload["timestamp"]
    assert "T" in payload["timestamp"]


def test_mit_tty_wird_die_konsole_gerendert(monkeypatch, capsys):
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True, raising=False)
    configure_logging("INFO")
    get_logger(__name__).info("lesbar")

    err = capsys.readouterr().err
    assert "lesbar" in err
    with pytest.raises(json.JSONDecodeError):
        json.loads(err.strip().splitlines()[-1])


def test_schwellwert_unterdrueckt_leisere_stufen(monkeypatch, capsys):
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False, raising=False)
    configure_logging("WARNING")
    log = get_logger(__name__)
    log.info("verschluckt")
    log.warning("durchgelassen")

    err = capsys.readouterr().err
    assert "verschluckt" not in err
    assert "durchgelassen" in err


def test_level_faellt_ohne_argument_auf_die_settings_zurueck(monkeypatch, capsys):
    monkeypatch.setenv("KINTSUGI_LOG_LEVEL", "ERROR")
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False, raising=False)
    from kintsugi.config import get_settings

    get_settings.cache_clear()
    configure_logging()
    log = get_logger(__name__)
    log.warning("verschluckt")
    log.error("durchgelassen")
    get_settings.cache_clear()

    err = capsys.readouterr().err
    assert "verschluckt" not in err
    assert "durchgelassen" in err


# --------------------------------------------------------------------------
# Disziplin im Rest der Codebasis
# --------------------------------------------------------------------------


def test_kein_modul_umgeht_das_logging_modul():
    """Kein direktes structlog.get_logger und kein print() unter kintsugi/."""
    offenders = []
    for path in (PROJECT_ROOT / "kintsugi").rglob("*.py"):
        if path.name == "logging.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "structlog.get_logger" in text:
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}: structlog.get_logger")
        if "print(" in text:
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}: print(")
    assert not offenders, "Am Logging vorbei:\n" + "\n".join(offenders)
