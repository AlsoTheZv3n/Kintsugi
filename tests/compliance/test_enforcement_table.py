"""Prueft, dass die Enforcement-Tabelle in COMPLIANCE.md nicht luegt.

Die README verspricht: „Rules that live only in a document get broken; the
enforcement table lists where each one is implemented." Dieser Test macht das
mechanisch nach. Fuer jede Zeile, die eine Durchsetzung behauptet, muss der
genannte Modulpfad importierbar UND der genannte Test ein pytest-Node sein, den
`--collect-only` auf genau einen Fall aufloest. Zeilen mit `UNENFORCED` werden
uebersprungen und gezaehlt — so ist die Luecke sichtbar, aber kein Fehler.
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPLIANCE = PROJECT_ROOT / "COMPLIANCE.md"

UNENFORCED = "UNENFORCED"


def _strip_cell(cell: str) -> str:
    return cell.strip().strip("`").strip()


def _table_rows() -> list[dict[str, str]]:
    """Zeilen der Tabelle mit den Spalten Rule/Enforced in/Failure mode/Test/Phase."""
    lines = COMPLIANCE.read_text(encoding="utf-8").splitlines()
    header_idx = next(
        (i for i, ln in enumerate(lines) if re.search(r"\|\s*Rule\s*\|.*Enforced in", ln)),
        None,
    )
    assert header_idx is not None, "Enforcement-Tabelle nicht gefunden"

    rows: list[dict[str, str]] = []
    for ln in lines[header_idx + 2 :]:  # Header + Trennzeile ueberspringen
        if not ln.strip().startswith("|"):
            break
        cells = [c for c in ln.strip().strip("|").split("|")]
        if len(cells) != 5:
            pytest.fail(f"Tabellenzeile hat {len(cells)} statt 5 Zellen: {ln!r}")
        rows.append(
            {
                "rule": _strip_cell(cells[0]),
                "enforced_in": _strip_cell(cells[1]),
                "failure": _strip_cell(cells[2]),
                "test": _strip_cell(cells[3]),
                "phase": _strip_cell(cells[4]),
            }
        )
    return rows


ROWS = _table_rows()
ENFORCED = [r for r in ROWS if r["enforced_in"] != UNENFORCED]


def test_es_gibt_ueberhaupt_zeilen():
    assert ROWS, "keine Tabellenzeilen geparst"


def test_jede_zeile_hat_fuenf_zellen():
    # _table_rows faellt bereits bei != 5, dieser Test macht die Zusicherung sichtbar.
    assert all(len(r) == 5 for r in ROWS)


def test_unenforced_zeilen_werden_gezaehlt_nicht_gefehlt(capsys):
    n = sum(1 for r in ROWS if r["enforced_in"] == UNENFORCED)
    print(f"UNENFORCED-Zeilen: {n} von {len(ROWS)}")
    assert n >= 0  # reine Zaehlung; die Ausgabe macht die Luecke sichtbar


@pytest.mark.parametrize("row", ENFORCED, ids=lambda r: r["rule"][:40])
def test_enforced_modulpfad_ist_importierbar(row: dict[str, str]):
    module_path = row["enforced_in"]
    assert re.fullmatch(r"kintsugi(\.\w+)*", module_path), (
        f"'Enforced in' muss UNENFORCED oder ein kintsugi.*-Pfad sein: {module_path!r}"
    )
    importlib.import_module(module_path)  # wirft, wenn nicht importierbar


@pytest.mark.parametrize("row", ENFORCED, ids=lambda r: r["rule"][:40])
def test_enforced_test_node_loest_auf_genau_einen_fall_auf(row: dict[str, str]):
    node = row["test"]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", node],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    collected = [ln for ln in result.stdout.splitlines() if "::" in ln and not ln.startswith("=")]
    assert len(collected) == 1, (
        f"Test-Node {node!r} loest auf {len(collected)} Faelle auf:\n{result.stdout}{result.stderr}"
    )
