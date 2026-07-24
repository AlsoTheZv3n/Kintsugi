"""Stellt sicher, dass CI die teuren Live-Tests nicht faehrt (I0.6.10).

Der Live-UPC-Walk dauert rund 35 Minuten und geht ins echte Netz. Kein
Workflow darf ihn versehentlich starten — weder ueber ``tests/live/`` noch mit
``-m live`` auf einem ``schedule:``-Trigger.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WORKFLOWS = sorted(Path(".github/workflows").glob("*.yml"))


def test_es_gibt_workflows_zu_pruefen():
    assert WORKFLOWS


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_kein_workflow_faehrt_live_tests(wf: Path):
    text = wf.read_text(encoding="utf-8")
    assert "tests/live" not in text, f"{wf.name} referenziert tests/live/"
    assert "-m live" not in text, f"{wf.name} fuehrt -m live aus"
    assert "-m 'live'" not in text
    assert '-m "live"' not in text
