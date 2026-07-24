"""QualityProfile: Form, Golden, Rundung, Schema-Feld-Herkunft (I1.1.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from kintsugi.packs.loader import load_pack
from kintsugi.quality.profile import QualityProfile, RangeViolation, RowCount
from pydantic import ValidationError

GOLDEN_DIR = Path(__file__).parent / "golden"


def _dumps(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _fixture() -> QualityProfile:
    return QualityProfile(
        fill_rate={
            "title": 1.0,
            "price": 0.99,
            "currency": 1.0,
            "availability": 0.8,
            "upc": 1.0,
        },
        range_violations={"price": RangeViolation(count=6, rate=0.06)},
        row_count=RowCount(actual=240, median_14d=238, deviation=0.008403),
        duplicate_rate=0.0,
        http={"200": 252},
        fetch_ms_p95=12,
        enum_violations={},
        natural_key_missing=0,
        insufficient_baseline=False,
        rows_considered=252,
        rows_written=240,
    )


def _valid_doc() -> dict:
    return _fixture().model_dump(mode="json")


def test_golden_ist_byte_identisch():
    assert (GOLDEN_DIR / "quality_profile.json").read_text("utf-8") == _dumps(_valid_doc())


def test_docs03_beispiel_erweitert_validiert():
    QualityProfile.model_validate(_valid_doc())


def test_unbekannter_key_wirft():
    with pytest.raises(ValidationError):
        QualityProfile.model_validate({**_valid_doc(), "bogus": 1})


def test_floats_auf_sechs_stellen_gerundet():
    prof = QualityProfile.model_validate({**_valid_doc(), "duplicate_rate": 0.123456789})
    assert prof.model_dump(mode="json")["duplicate_rate"] == 0.123457


def test_fill_rate_keys_gleich_schema_fields_inkl_currency():
    pack = load_pack("books.toscrape.com", "book", root=Path("packs"))
    fields = set(pack.schema_.fields)
    assert "currency" in fields  # F3: abgeleitet, aber deklariertes Feld
    prof = QualityProfile(**{**_valid_doc(), "fill_rate": dict.fromkeys(fields, 1.0)})
    assert set(prof.fill_rate) == fields


def test_quality_key_set_stammt_aus_reconciliation_golden():
    # Kein zweiter Literal-Key-Set: die elf Keys kommen aus dem #82-Golden.
    doc = json.loads((GOLDEN_DIR / "run_metrics.json").read_text("utf-8"))
    assert set(doc["quality"]) == set(QualityProfile.model_fields)
