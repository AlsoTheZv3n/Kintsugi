"""Prueft kintsugi/clock.py — den ausfuehrbaren Teil von ADR-009 Kontrakt 4."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from kintsugi.clock import run_started_at


def test_zeitstempel_ist_zeitzonenbewusst_und_utc():
    """Ein naiver Zeitstempel wuerde beim Schreiben in timestamptz falsch gedeutet."""
    stamp = run_started_at()
    assert stamp.tzinfo is not None, "valid_from muss zeitzonenbewusst sein"
    assert stamp.utcoffset() == timedelta(0), "muss UTC sein, nicht die lokale Zone"


def test_zeitstempel_liegt_plausibel_um_jetzt():
    vorher = datetime.now(UTC) - timedelta(seconds=5)
    stamp = run_started_at()
    nachher = datetime.now(UTC) + timedelta(seconds=5)
    assert vorher <= stamp <= nachher


def test_zwei_zeitzonen_ergeben_denselben_moment():
    """Vergleichbarkeit mit anders gezonten timestamptz-Werten aus der DB."""
    stamp = run_started_at()
    fernost = stamp.astimezone(timezone(timedelta(hours=9)))
    assert stamp == fernost, "derselbe Moment, nur andere Zonendarstellung"
