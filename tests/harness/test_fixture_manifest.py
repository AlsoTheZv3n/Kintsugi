"""Fixture-Manifest: Tamper-Erkennung, Ausnahmen, FixtureMeta-Form (I1.3.1)."""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import pytest
from kintsugi.harness.fixture_model import FixtureMeta
from kintsugi.harness.fixtures_cli import build_index, verify_index, write_index
from pydantic import ValidationError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _meta(**over) -> dict:
    base = {
        "url": "https://books.toscrape.com/x",
        "fetched_at": "2026-07-20T00:00:00+00:00",
        "http_status": 200,
        "content_type": "text/html",
        "content_hash": "a" * 64,
        "byte_size": 10,
        "fetcher": "httpx",
        "golden_label": "baseline",
    }
    base.update(over)
    return base


def test_committeter_baum_passt_zum_manifest():
    assert verify_index(FIXTURES) == []


def test_editiertes_byte_wird_erkannt(tmp_path):
    root = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, root)
    rel = "books.toscrape.com/book/golden/edge__very_long_value"
    target = root / Path(rel) / "page.html.gz"
    body = gzip.decompress(target.read_bytes())
    target.write_bytes(gzip.compress(body + b"<!-- getampert -->", mtime=0))
    offenders = verify_index(root)
    assert rel in offenders


def test_ausnahmeliste_und_fehlende_eintraege(tmp_path):
    index = build_index(FIXTURES)
    assert "_selftest/" in index["exempt"]
    assert "_synthetic/" in index["exempt"]
    assert "books.toscrape.com/book/corpus/" in index["exempt"]

    # Ein Golden-Pfad ohne Manifest-Eintrag faellt durch; ein exempter Pfad nicht.
    root = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, root)
    idx = json.loads((root / "index.json").read_text("utf-8"))
    removed = next(iter(idx["golden"]))
    del idx["golden"][removed]
    (root / "index.json").write_text(json.dumps(idx, indent=2) + "\n", encoding="utf-8")
    assert removed in verify_index(root)  # fehlender Golden-Eintrag = Fehler
    # Corpus (exempt) hat keinen Golden-Eintrag und loest trotzdem nichts aus.
    assert all("corpus" not in path for path in verify_index(root))


def test_fixture_meta_lehnt_synthetic_ohne_derived_from_ab():
    with pytest.raises(ValidationError):
        FixtureMeta.model_validate(_meta(synthetic=True, derived_from=None))


@pytest.mark.parametrize("bad", ["baseline_x", "edge:", "edge:Bad Slug", "corpus", "edge:UPPER"])
def test_fixture_meta_lehnt_ungueltiges_label_ab(bad):
    with pytest.raises(ValidationError):
        FixtureMeta.model_validate(_meta(golden_label=bad))


@pytest.mark.parametrize("good", ["baseline", "edge:out_of_stock", "edge:zero_results"])
def test_fixture_meta_akzeptiert_gueltiges_label(good):
    meta = FixtureMeta.model_validate(_meta(golden_label=good))
    assert meta.golden_label == good


def test_index_ist_regenerierbar(tmp_path):
    root = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, root)
    write_index(root)
    assert (root / "index.json").read_text("utf-8") == (FIXTURES / "index.json").read_text("utf-8")
