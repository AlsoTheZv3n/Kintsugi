"""Laufweite Uhrzeit.

Umsetzung von ADR-009, Kontrakt 4. ``valid_from`` (docs/03-data-model.md,
Abschnitt Silver) wird von der Anwendung als **ein** UTC-Zeitpunkt pro Lauf
geliefert, nicht vom spaltenseitigen ``DEFAULT now()``. Nur so teilen alle
Records eines Laufs denselben Zeitpunkt und die SCD-2-Intervalle aus ADR-007
verschachteln sich nie: bekaeme jede Zeile ihr eigenes ``now()``, laege das
``valid_to`` der abgeloesten Zeile Sekundenbruchteile neben dem ``valid_from``
der neuen, und ein Zeitpunktschnitt fiele in beide oder keine.

Der Schreibpfad (E0.9) nimmt den Wert einmal zu Laufbeginn und reicht ihn an
jeden Record desselben Laufs durch.
"""

from __future__ import annotations

from datetime import UTC, datetime


def run_started_at() -> datetime:
    """Ein zeitzonenbewusster UTC-Zeitstempel fuer den Laufbeginn.

    Bewusst ``UTC`` und ``tzinfo``-tragend: ``valid_from`` ist ``timestamptz``,
    und ein naiver Zeitstempel wuerde beim Schreiben in der lokalen Zone des
    Prozesses interpretiert — auf dieser Maschine eine andere als in CI oder im
    Container. Logzeilen (kintsugi/logging.py) tragen denselben ISO-8601-UTC,
    damit sie ohne Umrechnung zu ``run.started_at`` passen.
    """
    return datetime.now(UTC)
