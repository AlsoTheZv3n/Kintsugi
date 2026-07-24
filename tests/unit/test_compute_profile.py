"""compute_profile und triggers: reine Funktion, Nenner, Detektoren (I1.1.2)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from kintsugi.packs.loader import load_pack
from kintsugi.quality.history import HistoryStats
from kintsugi.quality.metrics import (
    ENUM_VIOLATION,
    FILL_RATE_DROP_VS_MEDIAN,
    NATURAL_KEY_MISSING,
    RANGE_VIOLATION,
    FetchStats,
    compute_profile,
    triggers,
)


def _pack():
    return load_pack("books.toscrape.com", "book", root=Path("packs"))


def _row(**over) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "A Book",
        "price": Decimal("10.00"),
        "currency": "GBP",
        "availability": 5,
        "upc": "a" * 16,
    }
    base.update(over)
    return base


def _stats(considered=100, duplicates=0, natural_key_missing=0) -> FetchStats:
    return FetchStats(
        rows_considered=considered,
        http={"200": considered},
        fetch_ms_p95=10,
        duplicates=duplicates,
        natural_key_missing=natural_key_missing,
    )


def _insufficient() -> HistoryStats:
    return HistoryStats(median_14d=None, qualifying_runs=0)


def test_nenner_ist_versuchte_seiten_nicht_records():
    records = [_row(title=f"T{i}") for i in range(70)]
    prof = compute_profile(records, _pack(), _insufficient(), _stats(considered=100))
    assert prof.fill_rate["title"] == 0.7
    assert prof.rows_written == 70
    # Ein record-basierter Nenner haette 1.0 gemeldet und den Ausfall versteckt.
    assert 70 / len(records) == 1.0


def test_ist_rein_keine_io(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("compute_profile darf kein I/O machen")

    monkeypatch.setattr("sqlalchemy.engine.Engine.connect", _boom)
    monkeypatch.setattr("httpx.Client.request", _boom)
    prof = compute_profile([_row()], _pack(), _insufficient(), _stats(considered=1))
    assert prof.rows_written == 1
    source = (Path("kintsugi") / "quality" / "metrics.py").read_text("utf-8")
    assert "asyncpg" not in source  # ADR-008: verbannt


def test_enum_verletzung_zaehlt_und_triggert_unbedingt():
    records = [_row() for _ in range(99)] + [_row(currency="XYZ")]
    prof = compute_profile(records, _pack(), _insufficient(), _stats(considered=100))
    assert prof.enum_violations["currency"] == 1
    trigs = triggers(prof, _pack(), _insufficient())  # insufficient_baseline True
    enum = [t for t in trigs if t.name == ENUM_VIOLATION]
    assert enum
    assert enum[0].escalate_only


def test_fehlender_natural_key_triggert_escalate_only():
    prof = compute_profile(
        [_row() for _ in range(100)], _pack(), _insufficient(), _stats(natural_key_missing=1)
    )
    assert prof.natural_key_missing == 1
    nk = [t for t in triggers(prof, _pack(), _insufficient()) if t.name == NATURAL_KEY_MISSING]
    assert nk
    assert nk[0].escalate_only


def test_bereichsverletzung_count_rate_und_schwelle():
    records = [_row() for _ in range(94)] + [_row(price=Decimal("99999")) for _ in range(6)]
    prof = compute_profile(records, _pack(), _insufficient(), _stats(considered=100))
    assert prof.range_violations["price"].count == 6
    assert prof.range_violations["price"].rate == 0.06
    assert any(t.name == RANGE_VIOLATION for t in triggers(prof, _pack(), _insufficient()))

    records4 = [_row() for _ in range(96)] + [_row(price=Decimal("99999")) for _ in range(4)]
    prof4 = compute_profile(records4, _pack(), _insufficient(), _stats(considered=100))
    assert prof4.range_violations["price"].rate == 0.04
    assert not any(t.name == RANGE_VIOLATION for t in triggers(prof4, _pack(), _insufficient()))


def test_fill_rate_drop_vs_median_haengt_am_baseline():
    records = [_row(title=f"T{i}") for i in range(70)]  # title 0.7
    prof_insuff = compute_profile(records, _pack(), _insufficient(), _stats(considered=100))
    assert not any(
        t.name == FILL_RATE_DROP_VS_MEDIAN for t in triggers(prof_insuff, _pack(), _insufficient())
    )

    valid = HistoryStats(median_14d=100, fill_rate_median={"title": 0.99}, qualifying_runs=3)
    prof_valid = compute_profile(records, _pack(), valid, _stats(considered=100))
    drop = [
        t
        for t in triggers(prof_valid, _pack(), valid)
        if t.name == FILL_RATE_DROP_VS_MEDIAN and t.field == "title"
    ]
    assert drop


def test_adr_010_ist_lokal_dokumentiert():
    # docs/ ist gitignored (docs-privat); nur lokal pruefbar, in CI uebersprungen.
    doc = Path("docs") / "09-decisions.md"
    if not doc.exists():
        pytest.skip("docs/ nicht vorhanden (gitignored, CI-Lauf)")
    text = doc.read_text("utf-8")
    assert "## ADR-010" in text
    assert "fill_rate_below_declared" in text
    assert "fill_rate_drop_vs_median" in text
