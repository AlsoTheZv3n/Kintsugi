"""Pandera-Schema aus der Site-Pack-Deklaration generiert (I1.2.1/I1.2.2).

docs/08 §Phase 1 („Pandera-Schemata aus der Site-Pack-Deklaration generieren")
und docs/02 §Feldsemantik. Die Qualitaetszusicherungen werden **erzeugt**, nie
je Entitaet handgeschrieben. Backend: Polars (ADR-011); pandas bleibt dem
Offline-Baseline-Job vorbehalten.

Der Generator liest ausschliesslich ``schema.fields`` (F3: ``currency`` ist
deklariert, hat aber keine ``extract.sources`` — es erscheint trotzdem als Spalte
mit ``Check.isin``). Frame-Level-Checks aus dem ``quality:``-Block (I1.2.2) kommen
als benannte Aggregatpruefungen dazu; ``row_count_deviation`` nur, wenn ein
14-Tage-Median hereingereicht wird.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandera.polars as pa
import polars as pl

if TYPE_CHECKING:
    from kintsugi.packs.model import SitePack

__all__ = ["schema_from_sitepack"]

# polars-Dtypes, gegen die pandera die Spalte prueft (ADR-011). ``decimal`` faellt
# bewusst auf ``Float64``: polars' Decimal-Dtype ist auf Precision/Scale streng und
# machte die Schema-Validierung gegen echte extrahierte Werte bruechig — fuer die
# Bereichs- und Enum-Qualitaetspruefungen genuegt Float64.
_DTYPE: dict[str, Any] = {
    "string": pl.Utf8,
    "decimal": pl.Float64,
    "integer": pl.Int64,
    "boolean": pl.Boolean,
    "datetime": pl.Datetime,
}


def _column_checks(fschema: object) -> list[pa.Check]:
    """Harte Zeilen-Checks: Enum und Pattern.

    ``sane_range`` steht **nicht** hier: docs/04 toleriert „Bereichsverletzungen
    ueber 5 % der Zeilen" — eine harte Spaltenpruefung liesse aber schon eine
    einzige Verletzung durchfallen und machte die 5%-Frame-Schwelle (I1.2.2)
    unerreichbar. Der Bereich wird deshalb ausschliesslich als
    ``max_range_violation_rate``-Frame-Check ausgewertet.
    """
    checks: list[pa.Check] = []
    enum = getattr(fschema, "enum", None)
    if enum is not None:
        checks.append(pa.Check.isin(list(enum), name="enum"))
    pattern = getattr(fschema, "pattern", None)
    if pattern is not None:
        checks.append(pa.Check.str_matches(pattern, name="pattern"))
    return checks


def schema_from_sitepack(pack: SitePack, *, median_14d: int | None = None) -> pa.DataFrameSchema:
    """Baut das ``DataFrameSchema`` fuer die Entitaet des Packs.

    ``median_14d`` speist den (optionalen) ``row_count_deviation``-Frame-Check
    (I1.2.2); ohne Median fehlt der Check ganz, statt still zu bestehen.
    """
    schema = pack.schema_
    natural_key = set(schema.natural_key)
    columns: dict[str, pa.Column] = {}
    for name, fschema in schema.fields.items():
        # required=true -> nullable=False; jede natural_key-Komponente ist non-null.
        nullable = not (fschema.required or name in natural_key)
        columns[name] = pa.Column(
            _DTYPE[fschema.type],
            checks=_column_checks(fschema) or None,
            nullable=nullable,
        )

    # Kein pandera-``unique=``: dessen String-Failure-Cases kollidieren beim
    # Concat mit den Bool-Failure-Cases der Frame-Checks (pandera.polars-Limit),
    # und strikte Eindeutigkeit widerspraeche der 2%-Duplikat-Toleranz. Die
    # Natural-Key-Eindeutigkeit traegt der ``max_duplicate_rate``-Frame-Check; die
    # echte Garantie ist ohnehin der DB-Index ``record_current`` (docs/03).
    return pa.DataFrameSchema(
        columns,
        checks=_frame_checks(pack, median_14d),
        strict=False,
    )


def _row_count(data: pa.PolarsData) -> int:
    return int(data.lazyframe.select(pl.len()).collect().item())


def _frame_checks(pack: SitePack, median_14d: int | None) -> list[pa.Check]:
    """Aggregatpruefungen aus dem ``quality:``-Block (I1.2.2).

    Frame-Level: sie schlagen als Frame-Fehler mit stabilem Check-Namen fehl,
    nie als Zeilenfehler. ``row_count_deviation`` erscheint nur mit gereichtem
    Median — bei ``median_14d=None`` fehlt der Check ganz, statt still zu bestehen.
    """
    quality = pack.quality
    natural_key = list(pack.schema_.natural_key)
    range_fields = [
        (name, fschema.sane_range)
        for name, fschema in pack.schema_.fields.items()
        if fschema.sane_range is not None
    ]
    checks: list[pa.Check] = []

    min_rows = quality.min_rows_per_run

    def _check_min_rows(data: pa.PolarsData) -> bool:
        return _row_count(data) >= min_rows

    checks.append(pa.Check(_check_min_rows, name="min_rows_per_run"))

    max_dup = quality.max_duplicate_rate

    def _check_duplicates(data: pa.PolarsData) -> bool:
        total = _row_count(data)
        if total == 0:
            return True
        unique = int(data.lazyframe.select(natural_key).unique().select(pl.len()).collect().item())
        return (total - unique) / total <= max_dup

    checks.append(pa.Check(_check_duplicates, name="max_duplicate_rate"))

    max_range = quality.max_range_violation_rate

    def _check_range_rate(data: pa.PolarsData) -> bool:
        total = _row_count(data)
        if total == 0 or not range_fields:
            return True
        mask = pl.lit(False)
        for name, (lo, hi) in range_fields:
            mask = mask | (pl.col(name) < lo) | (pl.col(name) > hi)
        violators = int(
            data.lazyframe.select(mask.alias("_v")).select(pl.col("_v").sum()).collect().item()
        )
        return violators / total <= max_range

    checks.append(pa.Check(_check_range_rate, name="max_range_violation_rate"))

    if median_14d is not None and median_14d > 0:
        limit = quality.row_count_deviation
        median = median_14d

        def _check_deviation(data: pa.PolarsData) -> bool:
            return abs(_row_count(data) - median) / median <= limit

        checks.append(pa.Check(_check_deviation, name="row_count_deviation"))

    return checks
