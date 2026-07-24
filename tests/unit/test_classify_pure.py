"""classify() ist rein, deterministisch und heiler-frei (I1.4.3)."""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

import httpx
import pytest
import sqlalchemy.engine
from kintsugi.classify import outcome as outcome_mod
from kintsugi.classify import precheck as precheck_mod
from kintsugi.classify.enums import HarnessOutcome, IncidentKind
from kintsugi.classify.outcome import Classification, classify
from kintsugi.classify.precheck import PrecheckResult
from kintsugi.harness.sources import PHASE1_SOURCES
from kintsugi.heal_protocol import HealerCapabilities, NullHealer
from kintsugi.packs.model import SitePack
from kintsugi.quality.profile import QualityProfile, RangeViolation, RowCount

_COMPLIANCE = {
    "tos_url": "https://example.test/",
    "tos_verdict": "permits",
    "tos_reviewed_at": "2026-07-21",
    "reviewed_by": "human:sven",
    "robots_checked_at": "2026-07-21",
    "public_content": True,
    "personal_data": False,
}


def _pack() -> SitePack:
    return SitePack.model_validate(
        {
            "apiVersion": "kintsugi/v1",
            "domain": "example.test",
            "entity": "thing",
            "version": 1,
            "discovery": {"strategy": "pagination", "url_template": "p-{n}.html"},
            "extract": {"sources": [{"kind": "css", "fields": {"upc": {"selector": ".upc"}}}]},
            "schema": {
                "natural_key": ["upc"],
                "fields": {
                    "upc": {"type": "string", "required": True},
                    "price": {"type": "decimal", "sane_range": [0, 1000]},
                    "currency": {"type": "string", "enum": ["GBP", "CHF", "EUR", "USD"]},
                },
            },
            "compliance": dict(_COMPLIANCE),
        }
    )


def _profile(
    *,
    fill_rate=None,
    range_violations=None,
    row_count=None,
    duplicate_rate=0.0,
    enum_violations=None,
    natural_key_missing=0,
    insufficient_baseline=False,
    rows_considered=100,
    rows_written=100,
) -> QualityProfile:
    # Feldnamen bewusst als Keyword-Parameter, nicht als String-Literale — der
    # volle Quality-Key-Set steht nur im Golden (Ein-Beleg-Regel, #82).
    return QualityProfile(
        fill_rate=fill_rate or {},
        range_violations=range_violations or {},
        row_count=row_count or RowCount(actual=100, median_14d=100, deviation=0.0),
        duplicate_rate=duplicate_rate,
        http={str(200): 100},
        fetch_ms_p95=10,
        enum_violations=enum_violations or {},
        natural_key_missing=natural_key_missing,
        insufficient_baseline=insufficient_baseline,
        rows_considered=rows_considered,
        rows_written=rows_written,
    )


_OK = PrecheckResult(verdict=precheck_mod.PrecheckVerdict.ok)

# Eine Tabelle, die jeden Signal-Typ einmal ausloest (Verdikt ok).
_EVERY_SIGNAL: list[tuple[str, QualityProfile, IncidentKind]] = [
    ("fill_rate", _profile(fill_rate={"upc": 0.0}), IncidentKind.fill_rate_drop),
    (
        "row_count",
        _profile(row_count=RowCount(actual=10, median_14d=100, deviation=-0.9)),
        IncidentKind.row_count_anomaly,
    ),
    (
        "range",
        _profile(range_violations={"price": RangeViolation(count=90, rate=0.9)}),
        IncidentKind.range_violation,
    ),
    ("enum", _profile(enum_violations={"currency": 3}), IncidentKind.enum_violation),
    ("natural_key", _profile(natural_key_missing=4), IncidentKind.natural_key_broken),
    ("duplicate", _profile(duplicate_rate=1.0), IncidentKind.duplicate_rate_anomaly),
]


def test_classify_deckt_jeden_signaltyp_ab():
    pack = _pack()
    for _name, profile, expected_kind in _EVERY_SIGNAL:
        result = classify(profile, _OK, pack, HealerCapabilities.NONE)
        assert isinstance(result, Classification)
        assert expected_kind in result.incident_kinds


def test_classify_bleibt_rein_wenn_io_vergiftet_ist(monkeypatch):
    """Selbst wenn httpx und SQLAlchemy beim Anfassen werfen, laeuft classify."""

    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("classify hat I/O angefasst")

    monkeypatch.setattr(httpx, "Client", boom)
    monkeypatch.setattr(httpx, "AsyncClient", boom)
    monkeypatch.setattr(sqlalchemy.engine.Engine, "connect", boom)

    pack = _pack()
    for _name, profile, _kind in _EVERY_SIGNAL:
        assert isinstance(classify(profile, _OK, pack, HealerCapabilities.NONE), Classification)


def test_classify_ist_deterministisch():
    pack = _pack()
    profile = _profile(fill_rate={"upc": 0.0}, duplicate_rate=1.0)
    a = classify(profile, _OK, pack, HealerCapabilities.NONE)
    b = classify(profile, _OK, pack, HealerCapabilities.NONE)
    assert a == b


def test_outcome_quelle_ohne_uhr_oder_zufall():
    src = pathlib.Path(outcome_mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("datetime.now", "time.time", "random."):
        assert forbidden not in src


def test_classify_module_referenziert_kein_asyncpg():
    for mod in (outcome_mod, precheck_mod):
        src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert "asyncpg" not in src


def test_kein_healer_modul_wird_importiert():
    """Subprozess: import outcome zieht kein kintsugi.heal.* — und heal/ existiert nicht."""
    assert not (pathlib.Path(outcome_mod.__file__).parents[2] / "kintsugi" / "heal").exists()
    code = textwrap.dedent(
        """
        import sys
        import kintsugi.classify.outcome  # noqa: F401
        bad = [m for m in sys.modules if m == "kintsugi.heal" or m.startswith("kintsugi.heal.")]
        assert not bad, bad
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_healer_capabilities_und_nullhealer():
    assert isinstance(HealerCapabilities.NONE, HealerCapabilities)
    assert HealerCapabilities.NONE.value == 0
    healer = NullHealer()
    assert healer.capabilities() is HealerCapabilities.NONE
    with pytest.raises(NotImplementedError):
        healer.propose(_pack())


def test_nullhealer_erzwingt_kein_auto_healed():
    pack = _pack()
    profile = _profile(fill_rate={"upc": 0.0})
    result = classify(profile, _OK, pack, HealerCapabilities.NONE)
    assert result.outcome is HarnessOutcome.escalated


def test_phase1_source_keys_sind_slash_frei():
    assert set(PHASE1_SOURCES) == {"books", "quotes_js", "scrapethissite_ajax"}
    for key, source in PHASE1_SOURCES.items():
        assert "/" not in key
        assert source.entry_url.startswith("http")
        assert source.key == key
