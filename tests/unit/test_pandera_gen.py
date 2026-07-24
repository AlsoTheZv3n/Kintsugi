"""schema_from_sitepack: Spalten, Checks, Eindeutigkeit, ein Ort (I1.2.1)."""

from __future__ import annotations

from pathlib import Path

from kintsugi.packs.loader import load_pack
from kintsugi.quality.pandera_gen import schema_from_sitepack


def _schema():
    return schema_from_sitepack(load_pack("books.toscrape.com", "book", root=Path("packs")))


def _check(column, name):
    return next(c for c in column.checks if c.name == name)


def test_genau_fuenf_spalten():
    assert set(_schema().columns) == {"title", "price", "currency", "availability", "upc"}


def test_currency_traegt_enum_check_mit_vier_werten():
    enum = _check(_schema().columns["currency"], "enum")
    assert enum.statistics["allowed_values"] == ["GBP", "CHF", "EUR", "USD"]


def test_upc_pattern_und_teil_der_eindeutigkeit():
    schema = _schema()
    pattern = _check(schema.columns["upc"], "pattern")
    assert pattern.statistics["pattern"] == "^[a-f0-9]{16}$"
    # Natural-Key-Eindeutigkeit als Frame-Check (nicht pandera-``unique=``, das mit
    # den Bool-Frame-Checks beim Concat kollidierte); die harte Garantie ist der
    # DB-Index record_current.
    assert "max_duplicate_rate" in {c.name for c in schema.checks}


def test_required_wird_nullable_false():
    schema = _schema()
    assert schema.columns["title"].nullable is False  # required
    assert schema.columns["upc"].nullable is False  # natural_key
    assert schema.columns["availability"].nullable is True  # required: false


def test_dataframeschema_nur_in_generator():
    hits = [
        path.name
        for path in Path("kintsugi").rglob("*.py")
        if "DataFrameSchema(" in path.read_text("utf-8")
    ]
    assert hits == ["pandera_gen.py"]
