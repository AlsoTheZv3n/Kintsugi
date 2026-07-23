"""Haelt die Zusicherungen von .github/workflows/ci.yml fest.

Ein CI-Workflow ist selbst ungetesteter Code, bis etwas ihn prueft. Zwei Dinge
sind sicherheitsrelevant: jede `uses:`-Action muss auf einen 40-Zeichen-Commit
gepinnt sein (ein Tag ist verschiebbar, ein Angreifer koennte ihn umhaengen),
und die Berechtigungen muessen auf Lesen beschraenkt bleiben.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"

SHA_PINNED = re.compile(r"@[0-9a-f]{40}(\s|$)")


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow(workflow_text: str) -> dict:
    return yaml.safe_load(workflow_text)


def test_jede_uses_action_ist_auf_einen_40_hex_commit_gepinnt(workflow_text: str):
    uses_lines = [line for line in workflow_text.splitlines() if re.search(r"\buses:", line)]
    assert uses_lines, "keine uses:-Zeile gefunden — der Test prueft dann nichts"
    unpinned = [line.strip() for line in uses_lines if not SHA_PINNED.search(line)]
    assert not unpinned, "nicht auf einen Commit-SHA gepinnt:\n" + "\n".join(unpinned)


def test_anzahl_gepinnter_entspricht_anzahl_uses(workflow_text: str):
    total = len(re.findall(r"\buses:\s*\S", workflow_text))
    pinned = len(re.findall(r"\buses:\s*\S+@[0-9a-f]{40}", workflow_text))
    assert pinned == total, f"{total} uses:, aber nur {pinned} SHA-gepinnt"


def test_berechtigungen_sind_nur_lesen(workflow: dict):
    assert workflow.get("permissions") == {"contents": "read"}
    assert "write" not in yaml.safe_dump(workflow.get("permissions", {}))


def test_kontaktadresse_wird_top_level_gesetzt(workflow: dict):
    assert workflow.get("env", {}).get("KINTSUGI_CONTACT") == "ops@example.invalid"


def test_jeder_job_hat_ein_timeout(workflow: dict):
    for name, job in workflow["jobs"].items():
        assert "timeout-minutes" in job, f"Job {name} ohne timeout-minutes"


def test_unit_job_waehlt_live_und_integration_ab(workflow_text: str):
    assert 'pytest -m "not live and not integration"' in workflow_text


def test_concurrency_bricht_veraltete_laeufe_ab(workflow: dict):
    conc = workflow.get("concurrency", {})
    assert conc.get("cancel-in-progress") is True


def test_uv_lock_check_faellt_bei_abgedrifteter_pyproject(tmp_path: Path):
    """Dieselbe Pruefung, die der Workflow ausfuehrt: uv lock --check."""
    if shutil.which("uv") is None:
        pytest.skip("uv nicht im PATH")

    work = tmp_path / "repo"
    work.mkdir()
    for name in ("pyproject.toml", "uv.lock", ".python-version", "README.md"):
        shutil.copy2(PROJECT_ROOT / name, work / name)
    shutil.copytree(PROJECT_ROOT / "kintsugi", work / "kintsugi")

    text = (work / "pyproject.toml").read_text(encoding="utf-8")
    text = text.replace('version = "0.1.0"', 'version = "0.1.1"')
    (work / "pyproject.toml").write_text(text, encoding="utf-8")

    result = subprocess.run(
        ["uv", "lock", "--check"],
        cwd=str(work),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, "uv lock --check bemerkte die Abdrift nicht"


def test_python_wird_auf_3_12_festgelegt():
    """setup-uv liest .python-version; die muss 3.12 nennen."""
    assert (PROJECT_ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"


def test_workflow_wird_von_actionlint_akzeptiert():
    """actionlint laeuft ueber den pre-commit-Hook, kein Host-Binary noetig."""
    if sys.platform == "win32" and shutil.which("uv") is None:
        pytest.skip("uv nicht im PATH")
    result = subprocess.run(
        ["uv", "run", "pre-commit", "run", "actionlint", "--files", str(WORKFLOW)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
