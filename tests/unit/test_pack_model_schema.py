"""Prueft SchemaSpec, FieldSchema, QualitySpec, HealingSpec, DeliverySpec (I0.6.3)."""

from __future__ import annotations

import pytest
from kintsugi.packs.model import (
    DeliverySpec,
    FieldSchema,
    QualitySpec,
    SchemaSpec,
    SitePack,
)
from pydantic import ValidationError


def test_pflichtfeld_defaultet_min_fill_rate_auf_eins():
    """docs/02: min_fill_rate ist der Wachhund; required darf nie ungeprueft sein."""
    assert FieldSchema(type="string", required=True).min_fill_rate == 1.0


def test_optionales_feld_defaultet_auf_null():
    assert FieldSchema(type="integer", required=False).min_fill_rate == 0.0


def test_min_fill_rate_ueber_eins_wird_abgelehnt():
    with pytest.raises(ValidationError):
        FieldSchema(type="string", required=True, min_fill_rate=1.5)


def test_kaputtes_pattern_wird_abgelehnt():
    with pytest.raises(ValidationError, match="regulaerer Ausdruck"):
        FieldSchema(type="string", required=True, pattern="(unbalanced")


def test_quality_defaults():
    q = QualitySpec()
    assert q.max_range_violation_rate == 0.05
    assert q.row_count_deviation == 0.30
    assert q.max_duplicate_rate == 0.02
    assert q.thresholds_source == "provisional"


def test_leerer_natural_key_wird_abgelehnt():
    with pytest.raises(ValidationError):
        SchemaSpec(natural_key=[], fields={"upc": FieldSchema(type="string", required=True)})


def test_thresholds_source_nur_provisional_oder_baseline():
    with pytest.raises(ValidationError):
        QualitySpec(thresholds_source="geraten")
    assert QualitySpec(thresholds_source="baseline").thresholds_source == "baseline"


def test_delivery_default_ist_postgres():
    assert DeliverySpec().sinks == ["postgres"]


def test_derived_from_wird_geparst():
    """ADR-013: currency ohne eigene Quelle, abgeleitet aus price."""
    fs = FieldSchema.model_validate(
        {
            "type": "string",
            "required": True,
            "enum": ["GBP", "CHF", "EUR", "USD"],
            "derived_from": {"source": "price", "transform": "currency_from_symbol"},
        }
    )
    assert fs.derived_from is not None
    assert fs.derived_from.source == "price"
    assert fs.derived_from.transform == "currency_from_symbol"


def test_vollstaendiges_pack_mit_allen_bloecken():
    pack = SitePack.model_validate(
        {
            "apiVersion": "kintsugi/v1",
            "domain": "books.toscrape.com",
            "entity": "book",
            "version": 1,
            "discovery": {"strategy": "pagination", "url_template": "p-{n}.html"},
            "extract": {"sources": [{"kind": "css", "fields": {"title": {"selector": "h1"}}}]},
            "schema": {
                "natural_key": ["upc"],
                "fields": {
                    "title": {"type": "string", "required": True, "min_fill_rate": 0.99},
                    "price": {"type": "decimal", "required": True},
                    "currency": {
                        "type": "string",
                        "required": True,
                        "enum": ["GBP", "CHF", "EUR", "USD"],
                        "derived_from": {"source": "price", "transform": "currency_from_symbol"},
                    },
                    "upc": {"type": "string", "required": True, "pattern": "^[a-f0-9]{16}$"},
                },
            },
            "quality": {"min_rows_per_run": 200, "thresholds_source": "provisional"},
            "healing": {"enabled": True, "escalate_on": ["field_removed"]},
            "delivery": {"sinks": ["postgres"]},
            "compliance": {
                "tos_url": "https://books.toscrape.com/",
                "tos_verdict": "permits",
                "tos_reviewed_at": "2026-07-21",
                "reviewed_by": "human:sven",
                "robots_checked_at": "2026-07-21",
                "public_content": True,
                "personal_data": False,
            },
        }
    )
    assert pack.schema_.natural_key == ["upc"]
    assert pack.quality.thresholds_source == "provisional"
    assert pack.healing.enabled is True
