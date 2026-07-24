"""Die Vorpruefung schliesst Qualitaetssignale kurz; Quota und Natural Key (I1.4.4)."""

from __future__ import annotations

import pytest
from kintsugi.classify.enums import HarnessOutcome, IncidentKind, PrecheckVerdict
from kintsugi.classify.outcome import classify
from kintsugi.classify.precheck import PrecheckResult, evaluate_precheck
from kintsugi.heal_protocol import HealerCapabilities
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


def _pack(**quality: object) -> SitePack:
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
            "quality": dict(quality) if quality else {},
            "healing": {"enabled": False, "max_auto_versions_per_window": 3, "window": "7d"},
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


def _catastrophic(*, natural_key_missing: int = 0) -> QualityProfile:
    """Alle Fill-Rates 0.0, Duplikatrate 1.0, jede Bereichspruefung verletzt."""
    return _profile(
        fill_rate={"upc": 0.0, "price": 0.0, "currency": 0.0},
        range_violations={"price": RangeViolation(count=100, rate=1.0)},
        duplicate_rate=1.0,
        row_count=RowCount(actual=0, median_14d=100, deviation=-1.0),
        enum_violations={"currency": 100},
        natural_key_missing=natural_key_missing,
    )


_NON_OK_VERDICTS = [
    PrecheckVerdict.unreachable,
    PrecheckVerdict.blocked,
    PrecheckVerdict.rate_limited,
    PrecheckVerdict.soft_404,
    PrecheckVerdict.quota_exhausted,
]


@pytest.mark.parametrize("verdict", _NON_OK_VERDICTS)
def test_nicht_ok_verdikt_erzwingt_no_action_trotz_katastrophe(verdict):
    result = classify(
        _catastrophic(),
        PrecheckResult(verdict=verdict),
        _pack(),
        HealerCapabilities.NONE,
    )
    assert result.outcome is HarnessOutcome.no_action
    # Nur der Fetch-Incident wird geoeffnet, kein einziges unterdruecktes Profil-Signal.
    assert IncidentKind.fill_rate_drop not in result.incident_kinds
    assert IncidentKind.duplicate_rate_anomaly not in result.incident_kinds


def test_blocked_plus_fehlender_natural_key_eskaliert():
    result = classify(
        _catastrophic(natural_key_missing=7),
        PrecheckResult(verdict=PrecheckVerdict.blocked),
        _pack(),
        HealerCapabilities.NONE,
    )
    assert result.outcome is HarnessOutcome.escalated
    assert IncidentKind.natural_key_broken in result.incident_kinds


def test_quota_erschoepft_gibt_healer_exhausted_critical():
    precheck = evaluate_precheck(max_auto_versions_per_window=3, auto_versions_in_window=3)
    assert precheck.verdict is PrecheckVerdict.quota_exhausted
    result = classify(_profile(), precheck, _pack(), HealerCapabilities.NONE)
    assert result.outcome is HarnessOutcome.no_action
    assert IncidentKind.healer_exhausted in result.incident_kinds
    healer_signal = next(
        s for s in result.signals if s.incident_kind is IncidentKind.healer_exhausted
    )
    assert healer_signal.severity == "critical"


def test_quota_unter_grenze_ist_ok():
    precheck = evaluate_precheck(max_auto_versions_per_window=3, auto_versions_in_window=2)
    assert precheck.verdict is PrecheckVerdict.ok


def test_enum_verletzung_soft_404_no_action_aber_ok_eskaliert():
    profile = _profile(enum_violations={"currency": 2})
    pack = _pack()

    blocked_soft404 = classify(
        profile, PrecheckResult(verdict=PrecheckVerdict.soft_404), pack, HealerCapabilities.NONE
    )
    assert blocked_soft404.outcome is HarnessOutcome.no_action

    ok = classify(
        profile, PrecheckResult(verdict=PrecheckVerdict.ok), pack, HealerCapabilities.NONE
    )
    assert ok.outcome is HarnessOutcome.escalated
    assert IncidentKind.enum_violation in ok.incident_kinds


@pytest.mark.parametrize("verdict", list(PrecheckVerdict))
@pytest.mark.parametrize("nkey", [0, 5])
def test_nie_auto_healed_unter_none(verdict, nkey):
    result = classify(
        _catastrophic(natural_key_missing=nkey),
        PrecheckResult(verdict=verdict),
        _pack(),
        HealerCapabilities.NONE,
    )
    assert result.outcome is not HarnessOutcome.auto_healed
