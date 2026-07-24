"""Prueft die fuenf statischen Site-Pack-Pruefungen (I0.6.8)."""

from __future__ import annotations

from kintsugi.packs.model import SitePack
from kintsugi.packs.validate import validate_pack


def _pack(**over: object) -> SitePack:
    base: dict[str, object] = {
        "apiVersion": "kintsugi/v1",
        "domain": "books.toscrape.com",
        "entity": "book",
        "version": 1,
        "discovery": {"strategy": "pagination", "url_template": "https://x/p-{n}.html"},
        "extract": {
            "sources": [
                {
                    "kind": "css",
                    "fields": {
                        "title": {"selector": "div.product_main > h1", "transform": ["strip"]},
                        "price": {
                            "selector": "p.price_color",
                            "transform": ["strip", "parse_currency"],
                        },
                        "upc": {"selector": "table tr:nth-child(1) td"},
                    },
                }
            ]
        },
        "schema": {
            "natural_key": ["upc"],
            "fields": {
                "title": {"type": "string", "required": True, "min_fill_rate": 0.99},
                "price": {"type": "decimal", "required": True, "min_fill_rate": 0.98},
                "currency": {
                    "type": "string",
                    "required": True,
                    "min_fill_rate": 1.0,
                    "enum": ["GBP", "CHF", "EUR", "USD"],
                    "derived_from": {"source": "price", "transform": "currency_from_symbol"},
                },
                "upc": {"type": "string", "required": True, "pattern": "^[a-f0-9]{16}$"},
            },
        },
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
    base.update(over)
    return SitePack.model_validate(base)


def _codes(pack: SitePack) -> set[str]:
    return {f.check_id for f in validate_pack(pack)}


def test_korrektes_pack_hat_keine_fehler():
    findings = validate_pack(_pack())
    assert [f for f in findings if f.severity == "error"] == []


def test_check1_kaputter_selektor(monkeypatch):
    pack = _pack()
    # Ein ungueltiger Selektor, den selectolax ablehnt.
    body = pack.model_dump(by_alias=True)
    body["extract"]["sources"][0]["fields"]["title"]["selector"] = "p["
    bad = SitePack.model_validate(body)
    findings = validate_pack(bad)
    hit = [f for f in findings if f.check_id == "selector_parse"]
    assert hit
    assert hit[0].key_path == "extract.sources[0].fields.title.selector"


def test_check2_feld_ohne_quelle():
    pack = _pack()
    body = pack.model_dump(by_alias=True)
    # publisher deklariert, aber weder css-Feld noch derived_from
    body["schema"]["fields"]["publisher"] = {
        "type": "string",
        "required": False,
        "min_fill_rate": 0.0,
    }
    bad = SitePack.model_validate(body)
    hit = [f for f in validate_pack(bad) if f.check_id == "field_without_source"]
    assert hit
    assert hit[0].key_path == "schema.fields.publisher"


def test_check2_derived_from_zaehlt_als_quelle():
    """currency hat kein css-Feld, aber derived_from — muss als Quelle gelten."""
    findings = validate_pack(_pack())
    assert not [f for f in findings if f.check_id == "field_without_source"]


def test_check3_natural_key_muss_required_sein():
    pack = _pack()
    body = pack.model_dump(by_alias=True)
    body["schema"]["fields"]["upc"]["required"] = False
    body["schema"]["fields"]["upc"]["min_fill_rate"] = 0.0
    bad = SitePack.model_validate(body)
    hit = [f for f in validate_pack(bad) if f.check_id == "natural_key_optional"]
    assert hit
    assert hit[0].key_path == "schema.fields.upc.required"


def test_check4_typunvertraegliche_kette():
    pack = _pack()
    body = pack.model_dump(by_alias=True)
    body["extract"]["sources"][0]["fields"]["price"]["transform"] = [
        "strip",
        "int_from_text",
        "parse_currency",
    ]
    bad = SitePack.model_validate(body)
    hit = [f for f in validate_pack(bad) if f.check_id == "transform_chain"]
    assert hit
    assert hit[0].key_path == "extract.sources[0].fields.price.transform"


def test_check4_gueltige_kette_meldet_nichts():
    findings = validate_pack(_pack())
    assert not [f for f in findings if f.check_id == "transform_chain"]


def test_check5_min_fill_rate_zu_niedrig():
    pack = _pack()
    body = pack.model_dump(by_alias=True)
    body["schema"]["fields"]["title"]["min_fill_rate"] = 0.2
    bad = SitePack.model_validate(body)
    hit = [f for f in validate_pack(bad) if f.check_id == "min_fill_rate_too_low"]
    assert hit
    assert hit[0].key_path == "schema.fields.title.min_fill_rate"
