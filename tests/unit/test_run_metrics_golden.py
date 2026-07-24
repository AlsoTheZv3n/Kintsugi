"""run.metrics namespaced: Golden, Block-Deckung, Ein-Beleg-Regel (I1.1.0/#82)."""

from __future__ import annotations

import json
from pathlib import Path

from kintsugi.quality.counters import RunCounters
from kintsugi.quality.profile import QualityProfile
from kintsugi.quality.run_metrics import CountersBlock, RunMetrics

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parent / "golden" / "run_metrics.json"


def test_golden_validiert_und_ist_byte_identisch():
    raw = GOLDEN.read_text("utf-8")
    doc = json.loads(raw)
    model = RunMetrics.model_validate(doc)  # wirft bei Formfehler
    reserialised = json.dumps(model.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
    assert reserialised == raw


def test_counters_block_deckt_run_counters():
    # Was der Runner schreibt (RunCounters.to_metrics), ist genau das, was der
    # CountersBlock erklaert — kein Key mehr, kein Key weniger.
    assert set(CountersBlock.model_fields) == set(RunCounters().to_metrics())


def test_run_metrics_hat_genau_zwei_bloecke():
    assert set(RunMetrics.model_fields) == {"counters", "quality"}


def test_genau_ein_exact_key_set_beleg_im_repo():
    # Der volle Quality-Key-Set steht woertlich nur im Golden (JSON), nie als
    # Literal in einem Python-Test — sonst gaebe es zwei Wahrheiten.
    quoted = [f'"{key}"' for key in QualityProfile.model_fields]
    offenders = [
        path.name
        for path in (PROJECT_ROOT / "tests").rglob("*.py")
        if all(token in path.read_text("utf-8") for token in quoted)
    ]
    assert offenders == [], f"voller Quality-Key-Set woertlich in: {offenders}"
