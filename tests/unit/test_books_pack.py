"""Prueft den ausgelieferten books.toscrape.com-Pack (I0.6.9)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from kintsugi.packs.loader import load_pack
from kintsugi.packs.validate import validate_pack

PACKS_ROOT = Path("packs")
PACK_FILE = PACKS_ROOT / "books.toscrape.com" / "book.yaml"


def _pack():
    return load_pack("books.toscrape.com", "book", root=PACKS_ROOT)


def test_pack_besteht_die_statischen_pruefungen():
    findings = validate_pack(_pack())
    errors = [f for f in findings if f.severity == "error"]
    assert errors == [], f"unerwartete Fehler: {errors}"


def test_pack_kernwerte():
    pack = _pack()
    assert pack.quality.min_rows_per_run == 200
    assert pack.quality.thresholds_source == "provisional"
    assert pack.healing.enabled is False
    assert pack.schema_.natural_key == ["upc"]


def test_pack_nutzt_pagination_ohne_sitemap():
    text = PACK_FILE.read_text(encoding="utf-8")
    assert "sitemap" not in text
    assert "strategy: pagination" in text
    assert "page-{n}.html" in text


def test_pack_validiert_gegen_das_json_schema():
    schema = json.loads(Path("schema/site-pack.schema.json").read_text(encoding="utf-8"))
    body = _pack().model_dump(by_alias=True, mode="json")
    jsonschema.validate(instance=body, schema=schema)


def test_currency_wird_abgeleitet():
    df = _pack().schema_.fields["currency"].derived_from
    assert df is not None
    assert df.source == "price"
    assert df.transform == "currency_from_symbol"


@pytest.mark.integration
def test_escalate_on_werte_sind_incident_kind_labels():
    """Jeder escalate_on-Wert muss ein Label des incident_kind-Enums am head sein."""
    from kintsugi.storage.db import get_engine
    from sqlalchemy import text

    eng = get_engine()
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'incident_kind'"
                )
            ).scalars()
            labels = set(rows)
    except Exception as exc:
        pytest.skip(f"kein postgres/Schema erreichbar: {exc}")

    if not labels:
        pytest.skip("incident_kind noch nicht migriert")
    fehlend = [v for v in _pack().healing.escalate_on if v not in labels]
    assert fehlend == [], f"escalate_on-Werte ohne incident_kind-Label: {fehlend}"
