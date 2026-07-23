"""Setzt ADR-008 ausfuehrbar durch: Phase 0/1 bleibt synchron und asyncpg-frei.

ADR-008 verbannt asyncpg und jede async-Faerbung aus Phase 0 und 1. Diese Regel
lebt nicht als Prosa, sondern als Gate: der Test durchsucht ``kintsugi/**`` und
schlaegt an, sobald ein ``async def``, ein ``await`` oder ein async-DB-Bezug
auftaucht.

Bewusst AST-basiert und nicht per Substring: eine Erwaehnung von „await" in
einem Docstring ist erlaubt, ``await x`` im Code nicht. Ein reiner Textscan
verwechselte beides.

Der Test prueft ausschliesslich getrackte Dateien unter ``kintsugi/`` und
niemals ``docs/`` — die Entwurfsdokumente sind lokal und in CI nicht vorhanden;
ein Guard, der sie laese, waere dort rot.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT_ROOT / "kintsugi"

# Namen, die eine async-DB-Anbindung verraten. ADR-008 schiebt sie auf Phase 3.
FORBIDDEN_NAMES = frozenset({"asyncpg", "create_async_engine", "AsyncSession", "AsyncEngine"})


def _python_files() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def test_es_gibt_ueberhaupt_module_zu_pruefen():
    """Ein leerer Baum wuerde den Guard stillschweigend bestehen lassen."""
    assert _python_files(), "keine Python-Dateien unter kintsugi/ gefunden"


def _async_offenders(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            found.append(f"async def {node.name} (Zeile {node.lineno})")
        elif isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith)):
            found.append(f"{type(node).__name__} (Zeile {node.lineno})")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(f"{node.id} (Zeile {node.lineno})")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.append(f".{node.attr} (Zeile {node.lineno})")
        elif isinstance(node, ast.alias) and node.name.split(".")[0] in FORBIDDEN_NAMES:
            found.append(f"import {node.name} (Zeile {getattr(node, 'lineno', '?')})")
    return found


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.relative_to(PROJECT_ROOT)))
def test_kein_async_und_kein_asyncpg_in_kintsugi(path: Path):
    """Kein Modul unter kintsugi/ ist async oder bindet asyncpg ein."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = _async_offenders(tree)
    assert not offenders, f"ADR-008 verletzt in {path.relative_to(PROJECT_ROOT)}: {offenders}"


def test_asyncpg_ist_in_der_umgebung_nicht_vorhanden():
    """ADR-008: asyncpg wird gar nicht erst installiert."""
    assert importlib.util.find_spec("asyncpg") is None, (
        "asyncpg ist installiert — ADR-008 verlangt seine vollstaendige Abwesenheit in Phase 0/1"
    )


def test_der_guard_erkennt_eine_eingeschmuggelte_async_def(tmp_path: Path):
    """Gegenprobe: ohne diese wuesste niemand, ob der Guard ueberhaupt greift."""
    victim = tmp_path / "schmuggel.py"
    victim.write_text(
        "async def holen():\n    await irgendwas()\n",
        encoding="utf-8",
    )
    tree = ast.parse(victim.read_text(encoding="utf-8"))
    offenders = _async_offenders(tree)
    assert any("async def" in o for o in offenders)
    assert any("Await" in o for o in offenders)


def test_der_guard_erkennt_create_async_engine(tmp_path: Path):
    victim = tmp_path / "engine.py"
    victim.write_text(
        "from sqlalchemy.ext.asyncio import create_async_engine\n"
        "engine = create_async_engine('postgresql+asyncpg://x')\n",
        encoding="utf-8",
    )
    tree = ast.parse(victim.read_text(encoding="utf-8"))
    assert _async_offenders(tree), "create_async_engine wurde nicht erkannt"


def test_await_in_einem_docstring_ist_erlaubt(tmp_path: Path):
    """Der AST-Ansatz darf Prosa nicht mit Code verwechseln."""
    victim = tmp_path / "prosa.py"
    victim.write_text(
        '"""Dieser Text erwaehnt await und asyncpg, ist aber synchroner Code."""\n'
        "def synchron():\n    return 1\n",
        encoding="utf-8",
    )
    tree = ast.parse(victim.read_text(encoding="utf-8"))
    assert _async_offenders(tree) == []
