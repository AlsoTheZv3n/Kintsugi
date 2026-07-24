"""Frame-Level-Checks aus dem quality-Block: Grenzen und Check-Namen (I1.2.2)."""

from __future__ import annotations

from pathlib import Path

import pandera.polars as pa
import polars as pl
from kintsugi.packs.loader import load_pack
from kintsugi.quality.pandera_gen import schema_from_sitepack


def _pack():
    return load_pack("books.toscrape.com", "book", root=Path("packs"))


def _frame(n: int, *, bad_price: int = 0, dup: int = 0) -> pl.DataFrame:
    prices = [51.77] * n
    for i in range(min(bad_price, n)):
        prices[i] = 99999.0  # ausserhalb sane_range [0.01, 10000]
    upcs = [f"{i:016x}" for i in range(n)]
    for i in range(1, dup + 1):
        upcs[i] = upcs[0]  # dup Duplikate des ersten Natural Key
    return pl.DataFrame(
        {
            "title": [f"T{i}" for i in range(n)],
            "price": pl.Series(prices, dtype=pl.Float64),
            "currency": ["GBP"] * n,
            "availability": pl.Series([5] * n, dtype=pl.Int64),
            "upc": upcs,
        }
    )


def _failing_check(schema: pa.DataFrameSchema, frame: pl.DataFrame) -> str | None:
    # Nicht-lazy: SchemaError nennt genau den einen fehlgeschlagenen Check
    # (pandera.polars concat-t gemischte Frame-Failure-Cases nicht sauber).
    try:
        schema.validate(frame)
    except pa.errors.SchemaError as exc:
        return getattr(exc.check, "name", str(exc.check))
    return None


def test_min_rows_per_run_grenze():
    schema = schema_from_sitepack(_pack())
    assert _failing_check(schema, _frame(200)) is None
    assert _failing_check(schema, _frame(199)) == "min_rows_per_run"


def test_max_duplicate_rate_grenze():
    schema = schema_from_sitepack(_pack())
    assert _failing_check(schema, _frame(210, dup=6)) == "max_duplicate_rate"  # 2.9 %
    assert _failing_check(schema, _frame(210, dup=4)) is None  # 1.9 %


def test_max_range_violation_rate_grenze():
    schema = schema_from_sitepack(_pack())
    assert _failing_check(schema, _frame(200, bad_price=12)) == "max_range_violation_rate"  # 6 %
    assert _failing_check(schema, _frame(200, bad_price=8)) is None  # 4 %


def test_row_count_deviation_nur_mit_median():
    schema_none = schema_from_sitepack(_pack())
    assert "row_count_deviation" not in {c.name for c in schema_none.checks}

    schema_med = schema_from_sitepack(_pack(), median_14d=1002)
    assert "row_count_deviation" in {c.name for c in schema_med.checks}
    assert _failing_check(schema_med, _frame(600)) == "row_count_deviation"  # 40 % Abweichung


def test_frame_check_namen_gleich_quality_keys():
    names = {c.name for c in schema_from_sitepack(_pack(), median_14d=1002).checks}
    assert {
        "min_rows_per_run",
        "max_duplicate_rate",
        "max_range_violation_rate",
        "row_count_deviation",
    } <= names
