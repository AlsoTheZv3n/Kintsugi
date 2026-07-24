"""Prueft die JSON-Schema-Generierung und den Drift-Check (I0.6.7)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from kintsugi.packs.jsonschema import _check, _serialise, generate

VALID_PACK = {
    "apiVersion": "kintsugi/v1",
    "domain": "books.toscrape.com",
    "entity": "book",
    "version": 1,
    "discovery": {"strategy": "pagination", "url_template": "https://x/p-{n}.html"},
    "extract": {"sources": [{"kind": "css", "fields": {"title": {"selector": "h1"}}}]},
    "schema": {
        "natural_key": ["upc"],
        "fields": {
            "upc": {
                "type": "string",
                "required": True,
                "min_fill_rate": 1.0,
                "pattern": "^[a-f0-9]{16}$",
            },
            "currency": {
                "type": "string",
                "required": True,
                "min_fill_rate": 1.0,
                "enum": ["GBP", "CHF", "EUR", "USD"],
                "derived_from": {"source": "price", "transform": "currency_from_symbol"},
            },
        },
    },
    "quality": {"thresholds_source": "provisional"},
    "compliance": {
        "tos_url": "https://x/",
        "tos_verdict": "permits",
        "tos_reviewed_at": "2026-07-21",
        "reviewed_by": "human:sven",
        "robots_checked_at": "2026-07-21",
        "public_content": True,
        "personal_data": False,
    },
}


def test_generate_ist_deterministisch():
    assert _serialise(generate()) == _serialise(generate())


def test_committetes_schema_ist_aktuell():
    assert _check() == 0


def test_beispielpack_validiert_gegen_das_schema():
    schema = json.loads(Path("schema/site-pack.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(instance=VALID_PACK, schema=schema)  # wirft bei Fehler


def test_schema_verwirft_unbekannten_top_level_key():
    schema = json.loads(Path("schema/site-pack.schema.json").read_text(encoding="utf-8"))
    with_bad = {**VALID_PACK, "unbekannt": 1}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=with_bad, schema=schema)


def test_check_erkennt_drift(tmp_path):
    """Ein manipuliertes Schema muss den Check rot machen."""
    tampered = tmp_path / "site-pack.schema.json"
    schema = generate()
    schema["title"] = "manipuliert"
    tampered.write_text(_serialise(schema), encoding="utf-8")
    assert _check(tampered) == 1


def test_check_meldet_fehlende_datei(tmp_path):
    assert _check(tmp_path / "gibt-es-nicht.json") == 1
