"""expected.json und baseline.json: Form, Pflichtfelder, Baseline-Drift (I1.3.4)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from kintsugi.extract.entity import extract_entity
from kintsugi.harness.expected_model import ExpectedFixture
from kintsugi.packs.loader import load_pack
from selectolax.lexbor import LexborHTMLParser

BOOK = Path(__file__).resolve().parents[2] / "fixtures" / "books.toscrape.com" / "book"
GOLDEN = BOOK / "golden"


def _pack():
    return load_pack("books.toscrape.com", "book", root=Path("packs"))


def _fixtures() -> list[Path]:
    return [
        meta.parent
        for meta in sorted(GOLDEN.rglob("meta.json"))
        if "golden_label" in json.loads(meta.read_text("utf-8"))
    ]


def _label(fixture: Path) -> str:
    return json.loads((fixture / "meta.json").read_text("utf-8"))["golden_label"]


def test_jede_expected_json_validiert():
    for fixture in _fixtures():
        path = fixture / "expected.json"
        assert path.is_file(), fixture
        ExpectedFixture.model_validate_json(path.read_text("utf-8"))  # wirft mit Pfad-Kontext


def test_pflichtfeldmenge_gleich_schema_inkl_currency():
    required = {name for name, fs in _pack().schema_.fields.items() if fs.required}
    assert "currency" in required  # F3
    for fixture in _fixtures():
        if _label(fixture) == "edge:zero_results":
            continue
        exp = ExpectedFixture.model_validate_json((fixture / "expected.json").read_text("utf-8"))
        assert {n for n, f in exp.fields.items() if f.required} == required


def test_zero_results_hat_null_zeilen():
    zero = next(f for f in _fixtures() if _label(f) == "edge:zero_results")
    exp = ExpectedFixture.model_validate_json((zero / "expected.json").read_text("utf-8"))
    assert exp.expected_row_count == 0
    assert exp.expected_natural_keys == []


def _recompute_baseline() -> dict:
    pack = _pack()
    optional = [name for name, fs in pack.schema_.fields.items() if not fs.required]
    nonnull = dict.fromkeys(optional, 0)
    detail = 0
    for fixture in _fixtures():
        if _label(fixture) == "edge:zero_results":
            continue
        detail += 1
        body = gzip.decompress((fixture / "page.html.gz").read_bytes()).decode("utf-8")
        values, _ = extract_entity(pack, LexborHTMLParser(body))
        for name in optional:
            if values.get(name) is not None:
                nonnull[name] += 1
    return {"corpus_size": detail, "optional_nonnull": dict(sorted(nonnull.items()))}


def test_baseline_json_stimmt_mit_corpus_ueberein():
    committed = json.loads((BOOK / "baseline.json").read_text("utf-8"))
    recomputed = _recompute_baseline()
    assert committed == recomputed, "baseline.json driftet — mit --update-baseline neu schreiben"
