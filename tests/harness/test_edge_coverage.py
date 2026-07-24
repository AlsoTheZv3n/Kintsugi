"""Edge-Taxonomie des Golden-Bestands: Abdeckung und Bodenschwelle (I1.3.3)."""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import pytest
import yaml
from kintsugi.packs.loader import load_pack
from selectolax.lexbor import LexborHTMLParser

BOOK = Path(__file__).resolve().parents[2] / "fixtures" / "books.toscrape.com" / "book"
GOLDEN = BOOK / "golden"
COVERAGE = BOOK.parent / "coverage.yaml"

FLOOR = 20
INSUFFICIENT = "insufficient_fixtures"


def _fixture_metas(root: Path) -> dict[Path, dict]:
    """FixtureMeta-Golden-Fixtures (mit ``golden_label``), Pfad -> meta."""
    metas: dict[Path, dict] = {}
    for meta_path in sorted(root.rglob("meta.json")):
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if "golden_label" in data:
            metas[meta_path.parent] = data
    return metas


def _coverage(root: Path) -> dict:
    spec = yaml.safe_load(COVERAGE.read_text(encoding="utf-8"))
    metas = _fixture_metas(root)
    if len(metas) < FLOOR:
        return {"rejected": INSUFFICIENT, "count": len(metas)}
    labels = {m["golden_label"] for m in metas.values()}
    waivers = spec.get("waivers") or {}
    missing = [r for r in spec["required"] if r not in labels and r not in waivers]
    return {"count": len(metas), "missing": missing, "labels": labels}


def test_taxonomie_ist_abgedeckt():
    report = _coverage(GOLDEN)
    assert report.get("rejected") is None
    assert report["count"] >= 30
    assert report["missing"] == []


def test_geloeschtes_edge_label_faellt_namentlich_durch(tmp_path):
    root = tmp_path / "golden"
    shutil.copytree(GOLDEN, root)
    shutil.rmtree(root / "edge__multilingual")
    report = _coverage(root)
    assert "edge:multilingual" in report["missing"]


def test_unter_zwanzig_wird_abgelehnt(tmp_path):
    root = tmp_path / "golden"
    shutil.copytree(GOLDEN, root)
    metas = sorted(_fixture_metas(root))
    # Bis auf 19 ausduennen.
    for path in metas[19:]:
        shutil.rmtree(path)
    assert _coverage(root)["rejected"] == INSUFFICIENT


def test_synthetische_fixtures_tragen_herkunft():
    for path, meta in _fixture_metas(GOLDEN).items():
        if meta.get("synthetic"):
            assert meta.get("derived_from"), path
            assert meta.get("edit"), path


def test_selektoren_loesen_auf_jeder_detailseite_auf():
    pack = load_pack("books.toscrape.com", "book", root=Path("packs"))
    selectors = {
        name: field.selector
        for source in pack.extract.sources
        for name, field in getattr(source, "fields", {}).items()
    }
    for path, meta in _fixture_metas(GOLDEN).items():
        if meta["golden_label"] == "edge:zero_results":
            continue  # Listenseite, keine Detailfelder
        html = gzip.decompress((path / "page.html.gz").read_bytes()).decode("utf-8")
        tree = LexborHTMLParser(html)
        for name, selector in selectors.items():
            assert tree.css_first(selector) is not None, f"{path}: {name} ({selector})"


@pytest.mark.parametrize("required", ["edge:out_of_stock", "edge:zero_results"])
def test_pflichtlabels_existieren(required):
    assert required in {m["golden_label"] for m in _fixture_metas(GOLDEN).values()}
