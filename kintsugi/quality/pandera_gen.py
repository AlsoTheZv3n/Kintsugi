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

# polars-Dtypes, gegen die pandera die Spalte prueft (ADR-011).
_DTYPE: dict[str, Any] = {
    "string": pl.Utf8,
    "decimal": pl.Decimal,
    "integer": pl.Int64,
    "boolean": pl.Boolean,
    "datetime": pl.Datetime,
}


def _column_checks(fschema: object) -> list[pa.Check]:
    checks: list[pa.Check] = []
    enum = getattr(fschema, "enum", None)
    if enum is not None:
        checks.append(pa.Check.isin(list(enum), name="enum"))
    sane_range = getattr(fschema, "sane_range", None)
    if sane_range is not None:
        lo, hi = sane_range
        checks.append(pa.Check.in_range(lo, hi, name="sane_range"))
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

    return pa.DataFrameSchema(
        columns,
        checks=_frame_checks(pack, median_14d),
        unique=list(schema.natural_key),  # Frame-Level-Eindeutigkeit des Natural Key
        strict=False,
    )


def _frame_checks(pack: SitePack, median_14d: int | None) -> list[pa.Check]:
    """Aggregatpruefungen aus dem ``quality:``-Block (I1.2.2 fuellt das)."""
    return []
