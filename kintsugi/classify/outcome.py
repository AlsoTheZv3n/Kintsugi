"""Der reine Outcome-Klassifikator (I1.4.3).

docs/08-roadmap.md §Phase 1 DoD („Ohne Heilung ist das erwartete Ergebnis
ueberall ``escalated`` bzw. ``no_action``"), docs/04-self-healing.md §Ausloeser +
§Vorpruefung, docs/06-observability.md §Alarmstufen.

``classify`` ist eine **reine Funktion**: kein Datenbank-Handle, kein Netz, kein
Dateisystem, keine Wanduhr, kein Zufall. Schwellwerte kommen aus ``pack.quality``
und ``pack.schema.fields`` (Daten, nicht Code — dieselbe eine Wahrheit, die auch
``quality.metrics.triggers`` liest), die gemessenen Werte aus dem
Qualitaetsprofil. Zwei Aufrufe mit gleichen Eingaben liefern gleiche Objekte.

Ablauf: erst das Vorpruefungs-Gate (``precheck``). Ein Nicht-``ok``-Verdikt
erzwingt ``no_action`` und unterdrueckt jedes Profil-Signal — nur der passende
Fetch-Incident (blocked/unreachable/rate_limited/healer_exhausted) wird geoeffnet.
Einzige Ausnahme: ein fehlender Natural Key eskaliert bedingungslos. Ist das
Verdikt ``ok``, entscheiden die Profil-Signale: eine reine
Zeilenzahl-Anomalie ist ``info`` und fuehrt zu ``no_action`` (docs/04-Negativfall
N06, „legitime Mengenaenderung"); jedes andere Signal eskaliert. ``auto_healed``
ist unter ``HealerCapabilities.NONE`` strukturell unerreichbar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from kintsugi.classify.enums import HarnessOutcome, IncidentKind, PrecheckVerdict
from kintsugi.classify.precheck import PrecheckResult, precheck_gate
from kintsugi.heal_protocol import HealerCapabilities
from kintsugi.quality.metrics import (
    DUPLICATE_RATE,
    ENUM_VIOLATION,
    FILL_RATE_BELOW_DECLARED,
    NATURAL_KEY_MISSING,
    RANGE_VIOLATION,
    ROW_COUNT_ANOMALY,
)

if TYPE_CHECKING:
    from kintsugi.packs.model import SitePack
    from kintsugi.quality.profile import QualityProfile

__all__ = [
    "INCIDENT_SEVERITY",
    "Classification",
    "Severity",
    "Signal",
    "classify",
    "derive_signals",
]

Severity = Literal["info", "warn", "critical"]

# Alarmstufe je Incident-Art (docs/06 §Alarmstufen). Severity ist eine reine
# Funktion der Incident-Art, kein Freiheitsgrad des Aufrufers — der Incident-
# Writer (#98) und die Signale hier lesen genau diese eine Tabelle.
INCIDENT_SEVERITY: dict[IncidentKind, Severity] = {
    IncidentKind.fill_rate_drop: "warn",
    IncidentKind.row_count_anomaly: "info",
    IncidentKind.range_violation: "warn",
    IncidentKind.schema_change: "critical",
    IncidentKind.field_removed: "critical",
    IncidentKind.unreachable: "warn",
    IncidentKind.blocked: "warn",
    IncidentKind.rate_limited: "warn",
    IncidentKind.healer_exhausted: "critical",
    IncidentKind.soft_404: "warn",
    IncidentKind.natural_key_broken: "critical",
    IncidentKind.enum_violation: "warn",
    IncidentKind.duplicate_rate_anomaly: "warn",
}

# Trigger-Name -> Incident-Art. Dieselben Namen wie quality.metrics.triggers.
_TRIGGER_TO_INCIDENT: dict[str, IncidentKind] = {
    FILL_RATE_BELOW_DECLARED: IncidentKind.fill_rate_drop,
    ROW_COUNT_ANOMALY: IncidentKind.row_count_anomaly,
    RANGE_VIOLATION: IncidentKind.range_violation,
    ENUM_VIOLATION: IncidentKind.enum_violation,
    NATURAL_KEY_MISSING: IncidentKind.natural_key_broken,
    DUPLICATE_RATE: IncidentKind.duplicate_rate_anomaly,
}

# Arten, die nie automatisch geheilt werden, immer eskalieren (docs/04
# §„Was niemals automatisch geheilt wird").
_ESCALATE_ONLY_KINDS: frozenset[IncidentKind] = frozenset(
    {
        IncidentKind.enum_violation,
        IncidentKind.natural_key_broken,
        IncidentKind.field_removed,
        IncidentKind.schema_change,
    }
)


class Signal(BaseModel):
    """Ein einzelnes Qualitaetssignal mit gemessenem Wert und Schwelle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    field: str | None
    observed: float
    threshold: float
    severity: Severity
    incident_kind: IncidentKind


class Classification(BaseModel):
    """Ergebnis des Klassifikators."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: HarnessOutcome
    signals: tuple[Signal, ...]
    incident_kinds: tuple[IncidentKind, ...]
    reason: str


def _signal(id_: str, field: str | None, observed: float, threshold: float) -> Signal:
    kind = _TRIGGER_TO_INCIDENT[id_]
    return Signal(
        id=id_,
        field=field,
        observed=observed,
        threshold=threshold,
        severity=INCIDENT_SEVERITY[kind],
        incident_kind=kind,
    )


def derive_signals(profile: QualityProfile, pack: SitePack) -> list[Signal]:
    """Die Profil-Signale (schwellwertbasiert, historie-frei).

    Deckt jeden Signal-Typ ausser ``fill_rate_drop_vs_median`` ab: dieser
    Median-Detektor braucht die Historie, die der reine Klassifikator bewusst
    nicht bekommt. Sein Incident (``fill_rate_drop``) faellt ohnehin mit dem
    deklarativen Detektor zusammen.
    """
    out: list[Signal] = []
    schema = pack.schema_
    quality = pack.quality

    for name, fschema in schema.fields.items():
        rate = profile.fill_rate.get(name)
        if rate is not None and rate < fschema.min_fill_rate:
            out.append(_signal(FILL_RATE_BELOW_DECLARED, name, rate, fschema.min_fill_rate))

    dev = profile.row_count.deviation
    if dev is not None and abs(dev) > quality.row_count_deviation:
        out.append(_signal(ROW_COUNT_ANOMALY, None, abs(dev), quality.row_count_deviation))

    for name, violation in profile.range_violations.items():
        if violation.rate > quality.max_range_violation_rate:
            out.append(
                _signal(RANGE_VIOLATION, name, violation.rate, quality.max_range_violation_rate)
            )

    # Unbedingte, escalate-only Signale (docs/04): Enum-Verletzung > 0, Natural Key.
    for name, count in profile.enum_violations.items():
        out.append(_signal(ENUM_VIOLATION, name, float(count), 0.0))
    if profile.natural_key_missing > 0:
        out.append(_signal(NATURAL_KEY_MISSING, None, float(profile.natural_key_missing), 0.0))

    if profile.duplicate_rate > quality.max_duplicate_rate:
        out.append(
            _signal(DUPLICATE_RATE, None, profile.duplicate_rate, quality.max_duplicate_rate)
        )

    return out


def _dedup_kinds(signals: tuple[Signal, ...]) -> tuple[IncidentKind, ...]:
    seen: dict[IncidentKind, None] = {}
    for sig in signals:
        seen.setdefault(sig.incident_kind, None)
    return tuple(seen)


def _verdict_signal(verdict: PrecheckVerdict, kind: IncidentKind) -> Signal:
    return Signal(
        id=verdict.value,
        field=None,
        observed=1.0,
        threshold=0.0,
        severity=INCIDENT_SEVERITY[kind],
        incident_kind=kind,
    )


def classify(
    profile: QualityProfile,
    precheck: PrecheckResult,
    pack: SitePack,
    capabilities: HealerCapabilities,
) -> Classification:
    """Der Outcome: ``no_action`` | ``escalated`` | ``auto_healed`` (Phase 1: nie)."""
    signals = derive_signals(profile, pack)
    nkey_signals = tuple(s for s in signals if s.incident_kind is IncidentKind.natural_key_broken)

    gate = precheck_gate(precheck, natural_key_missing=bool(nkey_signals))
    if gate is not None:
        out_signals: list[Signal] = []
        for kind in gate.incident_kinds:
            if kind is IncidentKind.natural_key_broken:
                out_signals.extend(nkey_signals)
            else:
                out_signals.append(_verdict_signal(precheck.verdict, kind))
        reason = (
            f"precheck {precheck.verdict.value}; natural key missing -> escalate"
            if gate.outcome is HarnessOutcome.escalated
            else f"precheck {precheck.verdict.value} -> no_action"
        )
        return Classification(
            outcome=gate.outcome,
            signals=tuple(out_signals),
            incident_kinds=gate.incident_kinds,
            reason=reason,
        )

    # Verdikt ok: die Profil-Signale entscheiden.
    signals_t = tuple(signals)
    if not signals_t:
        return Classification(
            outcome=HarnessOutcome.no_action,
            signals=(),
            incident_kinds=(),
            reason="clean run, no signals",
        )

    escalating = tuple(
        s for s in signals_t if s.incident_kind is not IncidentKind.row_count_anomaly
    )
    incident_kinds = _dedup_kinds(signals_t)

    if not escalating:
        # Nur eine Zeilenzahl-Anomalie: info, keine Heilung (docs/04 N06).
        return Classification(
            outcome=HarnessOutcome.no_action,
            signals=signals_t,
            incident_kinds=incident_kinds,
            reason="row_count_anomaly only (info) -> no_action",
        )

    can_heal = capabilities is not HealerCapabilities.NONE
    all_healable = all(s.incident_kind not in _ESCALATE_ONLY_KINDS for s in escalating)
    outcome = (
        HarnessOutcome.auto_healed if (can_heal and all_healable) else HarnessOutcome.escalated
    )
    return Classification(
        outcome=outcome,
        signals=signals_t,
        incident_kinds=incident_kinds,
        reason=f"{len(escalating)} escalating signal(s) -> {outcome.value}",
    )
