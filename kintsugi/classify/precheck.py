"""Die Vorpruefung: darf ueberhaupt geheilt werden (I1.4.4).

docs/04-self-healing.md §„Vorpruefung: darf ueberhaupt geheilt werden" („Faellt
einer davon, wird nicht geheilt") und §Ausloeser („Natural Key nicht extrahierbar
… immer Eskalation, nie Heilung").

Das Gate, das ``classify`` **zuerst** konsultiert, kennt drei Regeln:

1. **Jedes Verdikt ausser ``ok`` erzwingt ``no_action``** — egal wie
   katastrophal das Profil aussieht (alle Fill-Rates 0.0, Duplikatrate 1.0, jede
   Bereichspruefung verletzt). Das ist der Hauptschutz des Systems (docs/04): ohne
   ihn lernt der Heiler Selektoren aus einer Cookie-Banner-Seite.
2. **Ausnahme ``natural_key_missing``:** ein fehlender Natural Key eskaliert
   bedingungslos, auch unter einem Nicht-``ok``-Verdikt, weil ein falscher Natural
   Key den Bestand *rueckwirkend* korrumpiert (die Historisierung haengt an
   falschen Entitaeten). **Eskalieren ist nicht Heilen** — die Ausnahme
   widerspricht Regel 1 also nicht: sie uebergibt an den Menschen, sie repariert
   nichts.
3. **Auswertungsreihenfolge** (docs/04): ``unreachable``, ``blocked``,
   ``rate_limited``, ``soft_404``, ``quota_exhausted``.

Die Kontingentpruefung ist schon hier, nicht erst in Phase 2:
``auto_versions_in_window`` ist ein einfacher Ganzzahl-Input (der Aufrufer leitet
ihn aus ``site_pack``-Zeilen mit ``created_by LIKE 'healer:%'`` innerhalb von
``healing.window`` ab) und wird gegen ``max_auto_versions_per_window`` gehalten.
In Phase 1 ist der Input immer 0 — der Pfad ist trotzdem testbar.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from kintsugi.classify.enums import HarnessOutcome, IncidentKind, PrecheckVerdict
from kintsugi.fetch.block_detect import SignatureHit

__all__ = [
    "PRECHECK_VERDICT_TO_INCIDENT",
    "GateDecision",
    "PrecheckResult",
    "evaluate_precheck",
    "precheck_gate",
]


# Soft-404 wird bewusst auf ``unreachable`` abgebildet (docs/04 Negativtabelle,
# „Soft-404 mit Status 200 -> unreachable"): eine 200-Fehlerseite ist effektiv
# nicht erreichbarer Inhalt. Ein *echter* HTTP 404 (F1, der Pagination-Terminator)
# erreicht diese Abbildung nie — er ist gar kein soft_404-Verdikt.
PRECHECK_VERDICT_TO_INCIDENT: dict[PrecheckVerdict, IncidentKind] = {
    PrecheckVerdict.unreachable: IncidentKind.unreachable,
    PrecheckVerdict.blocked: IncidentKind.blocked,
    PrecheckVerdict.rate_limited: IncidentKind.rate_limited,
    PrecheckVerdict.soft_404: IncidentKind.unreachable,
    PrecheckVerdict.quota_exhausted: IncidentKind.healer_exhausted,
}


class PrecheckResult(BaseModel):
    """Ausgang der Vorpruefung: das Verdikt plus Beweismaterial fuers Incident-Dict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: PrecheckVerdict
    evidence: dict[str, object] = {}


@dataclass(frozen=True)
class GateDecision:
    """Was das Gate erzwingt, wenn das Verdikt nicht ``ok`` ist."""

    outcome: HarnessOutcome
    incident_kinds: tuple[IncidentKind, ...] = field(default_factory=tuple)


def evaluate_precheck(
    *,
    max_auto_versions_per_window: int,
    unreachable: bool = False,
    block_hit: SignatureHit | None = None,
    rate_limited: bool = False,
    soft_404_hit: SignatureHit | None = None,
    auto_versions_in_window: int = 0,
    evidence: dict[str, object] | None = None,
) -> PrecheckResult:
    """Leitet das Verdikt in der docs/04-Reihenfolge ab (erster Treffer gewinnt)."""
    base = dict(evidence or {})
    if unreachable:
        return PrecheckResult(verdict=PrecheckVerdict.unreachable, evidence=base)
    if block_hit is not None:
        return PrecheckResult(
            verdict=PrecheckVerdict.blocked,
            evidence={
                **base,
                "matched_signature": {"id": block_hit.id, "pattern": block_hit.pattern},
            },
        )
    if rate_limited:
        return PrecheckResult(verdict=PrecheckVerdict.rate_limited, evidence=base)
    if soft_404_hit is not None:
        return PrecheckResult(
            verdict=PrecheckVerdict.soft_404,
            evidence={
                **base,
                "matched_signature": {"id": soft_404_hit.id, "pattern": soft_404_hit.pattern},
            },
        )
    if auto_versions_in_window >= max_auto_versions_per_window:
        return PrecheckResult(
            verdict=PrecheckVerdict.quota_exhausted,
            evidence={
                **base,
                "auto_versions_in_window": auto_versions_in_window,
                "max_auto_versions_per_window": max_auto_versions_per_window,
            },
        )
    return PrecheckResult(verdict=PrecheckVerdict.ok, evidence=base)


def precheck_gate(precheck: PrecheckResult, *, natural_key_missing: bool) -> GateDecision | None:
    """Das Gate, das ``classify`` zuerst befragt.

    ``None`` heisst „Verdikt ok, weiter zur signalbasierten Klassifikation".
    Sonst erzwingt es ``no_action`` (Regel 1) — ausser bei fehlendem Natural Key,
    der bedingungslos eskaliert (Regel 2).
    """
    if precheck.verdict is PrecheckVerdict.ok:
        return None
    verdict_kind = PRECHECK_VERDICT_TO_INCIDENT[precheck.verdict]
    if natural_key_missing:
        return GateDecision(
            outcome=HarnessOutcome.escalated,
            incident_kinds=(verdict_kind, IncidentKind.natural_key_broken),
        )
    return GateDecision(outcome=HarnessOutcome.no_action, incident_kinds=(verdict_kind,))
