"""Prueft ExtractSpec und die diskriminierte Source-Union (I0.6.2)."""

from __future__ import annotations

import pytest
from kintsugi.packs.model import ExtractSpec, stub_execute
from pydantic import ValidationError


def test_priorisierte_reihenfolge_bleibt_positionell():
    spec = ExtractSpec.model_validate(
        {
            "sources": [
                {"kind": "jsonld", "type": "Product"},
                {"kind": "embedded_json", "script_id": "__NEXT_DATA__", "root": "props"},
                {"kind": "css", "fields": {"title": {"selector": "h1"}}},
            ]
        }
    )
    assert [s.kind for s in spec.sources] == ["jsonld", "embedded_json", "css"]


def test_unbekanntes_kind_wird_abgelehnt():
    with pytest.raises(ValidationError, match="kind"):
        ExtractSpec.model_validate({"sources": [{"kind": "bogus"}]})


def test_css_felder_werden_geparst():
    spec = ExtractSpec.model_validate(
        {
            "sources": [
                {
                    "kind": "css",
                    "fields": {
                        "price": {
                            "selector": "p.price_color",
                            "anchor_hint": "Preis mit Symbol",
                            "transform": ["strip", "parse_currency"],
                        }
                    },
                }
            ]
        }
    )
    css = spec.sources[0]
    assert css.fields["price"].transform == ["strip", "parse_currency"]
    assert css.fields["price"].anchor_hint == "Preis mit Symbol"


def test_embedded_json_braucht_locator():
    with pytest.raises(ValidationError, match="script_id oder js_var"):
        ExtractSpec.model_validate({"sources": [{"kind": "embedded_json"}]})


def test_embedded_json_akzeptiert_js_var():
    """F5: quotes.toscrape.com/js legt Daten als var-Zuweisung ohne id ab."""
    spec = ExtractSpec.model_validate(
        {"sources": [{"kind": "embedded_json", "js_var": "data"}]}
    )
    assert spec.sources[0].js_var == "data"


@pytest.mark.parametrize(
    ("kind", "phase"),
    [
        ("api", "Phase 5"),
        ("llm", "Phase 4"),
        ("jsonld", "Phase 1"),
        ("embedded_json", "Phase 1"),
        ("xhr", "Phase 1"),
    ],
)
def test_stub_executor_nennt_seine_phase(kind, phase):
    with pytest.raises(NotImplementedError, match=phase):
        stub_execute(kind)
