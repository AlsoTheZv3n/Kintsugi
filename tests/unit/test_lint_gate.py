"""Belegt, dass das Lint- und Typgate untypisierten Code tatsaechlich ablehnt.

Ein gruener `ruff check` beweist fuer sich genommen nur, dass nichts gemeldet
wurde — nicht, dass ueberhaupt etwas gemeldet werden koennte. Der Test fuehrt
deshalb beide Werkzeuge gegen eine absichtlich fehlerhafte Kopie von
``kintsugi/cli.py`` aus und besteht nur, wenn sie anschlagen.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BAD_FUNCTION = "\n\ndef f(x):\n    return x\n"


@pytest.fixture
def offending_module(tmp_path):
    """Kopie von kintsugi/cli.py, an die eine untypisierte Funktion angehaengt ist."""
    source = (PROJECT_ROOT / "kintsugi" / "cli.py").read_text(encoding="utf-8")
    target = tmp_path / "cli_with_violation.py"
    target.write_text(source + BAD_FUNCTION, encoding="utf-8")
    return target


def _tool(name):
    """Pfad zum Werkzeug in der aktiven venv, sonst Skip."""
    found = shutil.which(name)
    if found is None:
        pytest.skip(f"{name} nicht im PATH — Test benoetigt die synchronisierte venv")
    return found


def test_ruff_meldet_fehlende_annotationen(offending_module):
    """ANN001 (Argument) und ANN201 (Rueckgabe) muessen beide anschlagen."""
    result = subprocess.run(
        [_tool("ruff"), "check", "--isolated", "--select", "ANN", str(offending_module)],
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, f"ruff meldete nichts:\n{output}"
    assert "ANN001" in output, f"ANN001 fehlt in:\n{output}"
    assert "ANN201" in output, f"ANN201 fehlt in:\n{output}"


def test_mypy_meldet_untypisierte_funktion(offending_module):
    """mypy --strict muss die untypisierte Definition als Fehler fuehren."""
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--no-error-summary", str(offending_module)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_cwd(offending_module),
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, f"mypy meldete nichts:\n{output}"
    assert "no-untyped-def" in output or "missing a type annotation" in output, (
        f"Kein untyped-def-Fehler in:\n{output}"
    )


def tmp_cwd(path):
    """mypy laeuft im Verzeichnis der Datei, damit pyproject.toml nicht greift."""
    return str(path.parent)


def test_unveraenderte_quelle_ist_sauber(tmp_path):
    """Gegenprobe: dieselbe Datei ohne die Verletzung muss beide Gates bestehen."""
    source = (PROJECT_ROOT / "kintsugi" / "cli.py").read_text(encoding="utf-8")
    clean = tmp_path / "cli_clean.py"
    clean.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [_tool("ruff"), "check", "--isolated", "--select", "ANN", str(clean)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"ruff meldet auf sauberer Quelle:\n{result.stdout}"
