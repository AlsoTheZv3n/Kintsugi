"""compute_profile: das Qualitaetsprofil als reine Funktion (I1.1.2).

docs/04-self-healing.md §Ausloeser (Trigger-Tabelle) und docs/02-site-packs.md
§Feldsemantik (``min_fill_rate``, ``sane_range``, ``natural_key``). Kein
Datenbank-Handle, kein Netz, keine Wanduhr — jeder Eingang wird hereingereicht,
die ganze Qualitaetsschicht laeuft in Sekunden im CI.

Vier tragende Regeln:

1. **Nenner ist die Zahl versuchter Seiten, nicht der erzeugten Records.**
   ``rows_considered`` kommt aus ``fetch_stats``, ``rows_written`` ist
   ``len(records)``. Bei 30 % leeren Seiten (N05) meldete ein record-basierter
   Nenner ``fill_rate = 1.0`` und der Teilausfall bliebe unsichtbar.
2. **Bereichsverletzungen als count UND rate** (``rate = count/rows_considered``),
   damit „ueber 5 % der Zeilen" auswertbar ist.
3. **Unbedingte Trigger.** ``enum_violations[f] > 0`` oder ``natural_key_missing
   > 0`` loesen unabhaengig von ``insufficient_baseline`` aus und sind
   escalate-only, nie heilbar (docs/04, „immer Eskalation, nie Heilung").
4. **Zwei Fill-Rate-Detektoren** (ADR-010): ``fill_rate_below_declared`` (gegen
   ``min_fill_rate``) und ``fill_rate_drop_vs_median`` (gegen den 14-Tage-Median,
   unterdrueckt bei ``insufficient_baseline``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import polars as pl

from kintsugi.quality.profile import QualityProfile, RangeViolation, RowCount

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from kintsugi.packs.model import SitePack
    from kintsugi.quality.history import HistoryStats

__all__ = [
    "FetchStats",
    "Trigger",
    "compute_profile",
    "triggers",
]

# Die sieben Trigger-Namen (docs/04 §Ausloeser; Fill-Rate in zwei Detektoren).
FILL_RATE_BELOW_DECLARED = "fill_rate_below_declared"
FILL_RATE_DROP_VS_MEDIAN = "fill_rate_drop_vs_median"
ROW_COUNT_ANOMALY = "row_count_anomaly"
RANGE_VIOLATION = "range_violation"
ENUM_VIOLATION = "enum_violation"
DUPLICATE_RATE = "duplicate_rate"
NATURAL_KEY_MISSING = "natural_key_missing"


@dataclass(frozen=True)
class FetchStats:
    """Was der Runner ausserhalb der Records mitzaehlt (reiner Input)."""

    rows_considered: int
    http: dict[str, int]
    fetch_ms_p95: int
    duplicates: int
    natural_key_missing: int
    # Versionsbewusst unveraenderte Seiten (Kurzschluss VOR der Extraktion). Sie
    # sind gesund, nur ungeprueft, und fallen aus dem Nenner der Seiten-Raten —
    # sonst faerbte ein inkrementeller Lauf (die meisten Seiten unveraendert)
    # jede Fill-Rate faelschlich auf fast 0. Ausgefallene/leere Seiten bleiben
    # dagegen im Nenner (der Teilausfall N05 soll ja sichtbar sein).
    rows_unchanged: int = 0


@dataclass(frozen=True)
class Trigger:
    """Ein ausgeloestes Qualitaetssignal."""

    name: str
    field: str | None
    escalate_only: bool


def _empty(value: object) -> bool:
    return value is None or value == ""


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _fill_counts(records: Sequence[Mapping[str, object]], fields: list[str]) -> dict[str, int]:
    """Gefuellte Werte je Feld — die Spalten-Aggregation ueber polars."""
    if not records:
        return dict.fromkeys(fields, 0)
    frame = pl.DataFrame({f: [not _empty(r.get(f)) for r in records] for f in fields})
    return {f: int(frame[f].sum()) for f in fields}


def compute_profile(
    records: Sequence[Mapping[str, object]],
    pack: SitePack,
    history: HistoryStats,
    fetch_stats: FetchStats,
) -> QualityProfile:
    schema = pack.schema_
    fields = list(schema.fields)
    considered = fetch_stats.rows_considered
    # Nenner der Seiten-Raten: betrachtete minus die versionsbewusst
    # Unveraenderten. Der Zaehler (fill_counts) zaehlt nur ueber die extrahierten
    # Records, also muss der Nenner die gar nicht extrahierten Kurzschluss-Seiten
    # auslassen — sonst ist jeder inkrementelle Lauf ein Fill-Rate-Fehlalarm.
    denominator = max(considered - fetch_stats.rows_unchanged, 0)
    written = len(records)

    def _rate(count: int) -> float:
        return count / denominator if denominator else 0.0

    fill_counts = _fill_counts(records, fields)
    fill_rate = {f: _rate(fill_counts[f]) for f in fields}

    range_violations: dict[str, RangeViolation] = {}
    enum_violations: dict[str, int] = {}
    for name, fschema in schema.fields.items():
        values = [r.get(name) for r in records]
        if fschema.sane_range is not None:
            lo, hi = fschema.sane_range
            rcount = 0
            for value in values:
                if _is_number(value):
                    number = float(value)  # type: ignore[arg-type]
                    if number < lo or number > hi:
                        rcount += 1
            if rcount:
                range_violations[name] = RangeViolation(count=rcount, rate=_rate(rcount))
        if fschema.enum is not None:
            ecount = sum(1 for value in values if value is not None and value not in fschema.enum)
            if ecount:
                enum_violations[name] = ecount

    median = history.median_14d
    deviation = (written - median) / median if median else None

    return QualityProfile(
        fill_rate=fill_rate,
        range_violations=range_violations,
        row_count=RowCount(actual=written, median_14d=median, deviation=deviation),
        duplicate_rate=_rate(fetch_stats.duplicates),
        http=dict(fetch_stats.http),
        fetch_ms_p95=fetch_stats.fetch_ms_p95,
        enum_violations=enum_violations,
        natural_key_missing=fetch_stats.natural_key_missing,
        insufficient_baseline=history.insufficient_baseline,
        rows_considered=considered,
        rows_written=written,
    )


def triggers(profile: QualityProfile, pack: SitePack, history: HistoryStats) -> list[Trigger]:
    """Die ausgeloesten Signale. ``history`` liefert die Feld-Medianwerte.

    Der Median lebt in ``HistoryStats``, nicht im Profil (das ein fester
    11-Schluessel-Vertrag ist), deshalb nimmt ``triggers`` ihn als dritten Input.
    """
    out: list[Trigger] = []
    schema = pack.schema_
    quality = pack.quality

    for name, fschema in schema.fields.items():
        rate = profile.fill_rate.get(name)
        if rate is not None and rate < fschema.min_fill_rate:
            out.append(Trigger(FILL_RATE_BELOW_DECLARED, name, escalate_only=False))

    if not profile.insufficient_baseline:
        for name in schema.fields:
            field_median = history.fill_rate_median.get(name)
            rate = profile.fill_rate.get(name)
            if field_median is not None and rate is not None and rate < field_median:
                out.append(Trigger(FILL_RATE_DROP_VS_MEDIAN, name, escalate_only=False))

    dev = profile.row_count.deviation
    if dev is not None and abs(dev) > quality.row_count_deviation:
        out.append(Trigger(ROW_COUNT_ANOMALY, None, escalate_only=False))

    for name, violation in profile.range_violations.items():
        if violation.rate > quality.max_range_violation_rate:
            out.append(Trigger(RANGE_VIOLATION, name, escalate_only=False))

    # Unbedingt und escalate-only (docs/04, pack.healing.escalate_on).
    for name in profile.enum_violations:
        out.append(Trigger(ENUM_VIOLATION, name, escalate_only=True))
    if profile.natural_key_missing > 0:
        out.append(Trigger(NATURAL_KEY_MISSING, None, escalate_only=True))

    if profile.duplicate_rate > quality.max_duplicate_rate:
        out.append(Trigger(DUPLICATE_RATE, None, escalate_only=False))

    return out
