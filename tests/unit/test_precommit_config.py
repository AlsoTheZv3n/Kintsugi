"""Prueft die Zusicherungen, die .pre-commit-config.yaml treffen muss.

Zwei davon sind nicht kosmetisch. Eine unversionierte `rev` macht den
Commit-Hook von einem fremden Branch abhaengig und damit unreproduzierbar. Und
ein Hook, der Dateien unter `fixtures/` anfasst, aendert den sha256 von
Golden Snapshots — dem byte-genauen Beweismaterial, auf dem das gesamte
Freigabe-Gate aus docs/04-self-healing.md steht.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / ".pre-commit-config.yaml"
CONTRIBUTING = PROJECT_ROOT / "CONTRIBUTING.md"


@pytest.fixture(scope="module")
def config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_jedes_fremde_repo_ist_auf_eine_rev_gepinnt(config):
    """Kein HEAD, kein Branchname — sonst ist der Hook nicht reproduzierbar."""
    unpinned = []
    for repo in config["repos"]:
        if repo["repo"] == "local":
            assert "rev" not in repo, "local-Repos duerfen keine rev tragen"
            continue
        rev = repo.get("rev")
        if not rev or rev.upper() == "HEAD" or rev in {"main", "master"}:
            unpinned.append(f"{repo['repo']}: {rev!r}")
    assert not unpinned, "Nicht gepinnte Hooks:\n" + "\n".join(unpinned)


def test_actionlint_ist_verdrahtet(config):
    """actionlint liegt nicht als Binary vor und kommt nur ueber pre-commit."""
    ids = {h["id"] for repo in config["repos"] for h in repo["hooks"]}
    assert "actionlint" in ids


def test_fixtures_sind_von_den_schreibenden_hooks_ausgenommen(config):
    """end-of-file-fixer und trailing-whitespace duerfen fixtures/ nicht anfassen."""
    for repo in config["repos"]:
        for hook in repo["hooks"]:
            if hook["id"] in {"end-of-file-fixer", "trailing-whitespace"}:
                assert hook.get("exclude") == "^fixtures/", (
                    f"{hook['id']} ohne exclude auf fixtures/ — das aendert "
                    "den sha256 von Golden Snapshots"
                )


@pytest.mark.slow
def test_hooks_lassen_fixture_dateien_byte_identisch(tmp_path):
    """Gegenprobe am echten Baum: eine Fixture-Datei bleibt unveraendert.

    Die Datei bekommt bewusst nachlaufende Leerzeichen und keine abschliessende
    Zeilenschaltung — genau das, was die beiden Hooks sonst reparieren wuerden.
    """
    victim = PROJECT_ROOT / "fixtures" / "_precommit_probe.txt"
    payload = b"letzte Zeile mit Leerzeichen   "
    victim.write_bytes(payload)
    try:
        before = hashlib.sha256(victim.read_bytes()).hexdigest()
        subprocess.run(
            ["uv", "run", "pre-commit", "run", "--all-files"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        after = hashlib.sha256(victim.read_bytes()).hexdigest()
        assert before == after, "Ein Hook hat eine Datei unter fixtures/ veraendert"
    finally:
        victim.unlink(missing_ok=True)


@pytest.mark.slow
def test_uv_lock_hook_schlaegt_bei_unversiegelter_abhaengigkeit_an(tmp_path):
    """Eine neue Abhaengigkeit ohne erneutes Lock muss den Hook rot machen."""
    if shutil.which("git") is None:
        pytest.skip("git nicht im PATH")

    work = tmp_path / "repo"
    work.mkdir()
    for name in ("pyproject.toml", "uv.lock", ".python-version", "README.md"):
        shutil.copy2(PROJECT_ROOT / name, work / name)
    shutil.copytree(PROJECT_ROOT / "kintsugi", work / "kintsugi")

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    uv_repo = next(r for r in config["repos"] if "uv-pre-commit" in r["repo"])
    (work / ".pre-commit-config.yaml").write_text(
        yaml.safe_dump({"repos": [uv_repo]}, sort_keys=False), encoding="utf-8"
    )

    subprocess.run(["git", "init", "-q"], cwd=str(work), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(work), check=True)

    # Abhaengigkeit ergaenzen, aber NICHT neu locken.
    text = (work / "pyproject.toml").read_text(encoding="utf-8")
    text = text.replace('"pandera>=0.32.1",', '"pandera>=0.32.1",\n    "attrs>=24",')
    (work / "pyproject.toml").write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(work), check=True)

    result = subprocess.run(
        [sys.executable, "-m", "pre_commit", "run", "uv-lock", "--all-files"],
        cwd=str(work),
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, f"uv-lock hat die fehlende Versiegelung nicht bemerkt:\n{output}"


def test_contributing_nennt_die_einrichtung_und_die_kriterienregel():
    """Die Regel fuer Akzeptanzkriterien ist bindend und muss dokumentiert sein."""
    text = CONTRIBUTING.read_text(encoding="utf-8")
    assert "uv sync" in text
    assert "uv run pre-commit install" in text
    assert "Akzeptanzkriterien" in text
    assert "# bash" in text
    assert "pytest-Knoten" in text
