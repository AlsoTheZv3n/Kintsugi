"""QualityProfile: der ``quality``-Block des ``run.metrics``-Dokuments.

docs/03-data-model.md §Laeufe (das ``metrics``-JSON) und docs/04-self-healing.md
§Ausloeser (Trigger-Tabelle). ``model_dump(mode="json")`` ist genau das Dokument,
das unter ``run.metrics["quality"]`` landet (namespaced-Form, #82) — die Form ist
ein Vertrag, kein internes Detail. Genau elf Top-Level-Schluessel, ``extra="forbid"``.

Zwei bewusste Erweiterungen gegenueber dem docs/03-Beispiel:

- ``enum_violations`` und ``natural_key_missing`` kommen dazu, weil die
  Trigger-Tabelle in docs/04 Signale braucht, die das docs/03-Beispiel auslaesst
  (Enum-Verletzung „ueber 0", Natural Key „immer Eskalation, nie Heilung").
- ``RangeViolation`` traegt ``{count, rate}`` statt docs/03' blanker Ganzzahl,
  weil docs/04' Schwelle „ueber 5 % der Zeilen" lautet und ein blanker Count
  nicht mit einem Prozentsatz vergleichbar ist.

Zusaetzlich fuer ADR-002' Beobachtungsmodus und einen ehrlichen Nenner:
``insufficient_baseline``, ``rows_considered``, ``rows_written``.

Metrik-Schluessel stammen aus ``schema.fields`` des Packs, nicht aus
``extract.sources`` (F3): ``currency`` hat keine Extraktionsquelle, wird aus dem
Preissymbol abgeleitet, ist aber ein deklariertes, pflichtiges, enum-beschraenktes
Feld und muss deshalb in ``fill_rate`` und ``enum_violations`` erscheinen.

Floats werden bei der Serialisierung auf 6 Nachkommastellen gerundet, damit der
Snapshot plattformstabil ist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, field_serializer

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["QualityProfile", "RangeViolation", "RowCount"]


def _round6(value: float) -> float:
    return round(value, 6)


class RangeViolation(BaseModel):
    """Bereichsverletzungen eines Feldes als Anzahl **und** Rate."""

    model_config = ConfigDict(extra="forbid")

    count: int
    rate: float

    @field_serializer("rate")
    def _ser_rate(self, value: float) -> float:
        return _round6(value)


class RowCount(BaseModel):
    """Zeilenzahl des Laufs gegen den 14-Tage-Median."""

    model_config = ConfigDict(extra="forbid")

    actual: int
    median_14d: int | None = None
    deviation: float | None = None

    @field_serializer("deviation")
    def _ser_deviation(self, value: float | None) -> float | None:
        return None if value is None else _round6(value)


class QualityProfile(BaseModel):
    """Der ``quality``-Block. Genau elf Top-Level-Schluessel, extra verboten."""

    model_config = ConfigDict(extra="forbid")

    fill_rate: dict[str, float]
    range_violations: dict[str, RangeViolation]
    row_count: RowCount
    duplicate_rate: float
    http: dict[str, int]
    fetch_ms_p95: int
    enum_violations: dict[str, int]
    natural_key_missing: int
    insufficient_baseline: bool
    rows_considered: int
    rows_written: int

    @field_serializer("fill_rate")
    def _ser_fill_rate(self, value: dict[str, float]) -> dict[str, float]:
        return {key: _round6(rate) for key, rate in value.items()}

    @field_serializer("duplicate_rate")
    def _ser_duplicate_rate(self, value: float) -> float:
        return _round6(value)

    def iter_metrics(
        self,
        domain: str,
        entity: str,
        *,
        duration_seconds: float = 0.0,
        status: str = "ok",
        fetcher: str = "httpx",
    ) -> Iterator[tuple[str, dict[str, str], float]]:
        """Flache Prometheus-Tupel (Name, Labels, Wert) — docs/06 §Metriken.

        Der Phase-3-Exporter ist ein reiner Adapter darueber; kein
        ``prometheus_client`` in Phase 1. ``duration`` und der Fetcher-Namensraum
        stehen nicht im 11-Schluessel-Profil und werden hereingereicht — sonst
        erreichten sie den Exporter nie. ``row_count_deviation`` wird bei
        ``insufficient_baseline`` gar nicht emittiert (0.0 laese sich als „keine
        Abweichung").
        """
        de = {"domain": domain, "entity": entity}
        for field_name, rate in self.fill_rate.items():
            yield "kintsugi_field_fill_rate", {**de, "field": field_name}, rate
        for field_name, violation in self.range_violations.items():
            yield (
                "kintsugi_range_violations_total",
                {**de, "field": field_name},
                float(violation.count),
            )
        if not self.insufficient_baseline and self.row_count.deviation is not None:
            yield "kintsugi_row_count_deviation", de, self.row_count.deviation
        yield "kintsugi_duplicate_rate", de, self.duplicate_rate
        yield "kintsugi_rows_extracted_total", de, float(self.rows_written)
        yield (
            "kintsugi_run_duration_seconds",
            {**de, "status": status},
            duration_seconds,
        )
        for http_status, count in self.http.items():
            yield (
                "kintsugi_pages_fetched_total",
                {"domain": domain, "fetcher": fetcher, "http_status": http_status},
                float(count),
            )
