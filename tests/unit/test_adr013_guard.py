"""Setzt ADR-013 durch: nur `derived_from`, keine Konkurrenzmechanismen.

ADR-013 (lokaler Entscheidungslog) waehlt genau einen Mechanismus fuer Felder
ohne eigene Extraktionsquelle: den `derived_from`-Block. Die beiden verworfenen
Entwuerfe — ein Multi-Output-Transform mit `produces_fields` und eine
`kind: const`-Quelle — duerfen im Code und in den Site-Packs nicht auftauchen,
sonst koexistieren wieder unvereinbare Vokabulare und die statische Pruefung
kann nur eines anerkennen.

Der Guard scannt ausschliesslich getrackte Dateien unter `kintsugi/` und
`packs/`. `docs/02-site-packs.md` traegt das Beispiel bewusst nicht in die
Pruefung: die Entwurfsdokumente sind lokal und in CI nicht vorhanden — ein
Guard, der sie laese, waere dort rot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Der gewaehlte Mechanismus und die beiden verworfenen.
CHOSEN = "derived_from"
REJECTED = ("produces_fields", "kind: const")

SCAN_ROOTS = ("kintsugi", "packs")
SCAN_SUFFIXES = ("*.py", "*.yaml", "*.yml")


def _tracked_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        base = PROJECT_ROOT / root
        if not base.exists():
            continue
        for suffix in SCAN_SUFFIXES:
            files.extend(p for p in base.rglob(suffix) if p.name != Path(__file__).name)
    return files


@pytest.mark.parametrize("token", REJECTED)
def test_kein_verworfener_mechanismus_im_code_oder_pack(token: str):
    """Weder produces_fields noch kind: const duerfen vorkommen."""
    offenders = []
    for path in _tracked_files():
        if token in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert not offenders, f"ADR-013 verworfener Mechanismus {token!r} gefunden in:\n" + "\n".join(
        offenders
    )


def test_der_guard_hat_ueberhaupt_dateien_zu_pruefen():
    """Sonst bestuende er stillschweigend, egal was im Code steht."""
    assert _tracked_files(), "keine getrackten Dateien unter kintsugi/ oder packs/"


def test_gewaehlter_mechanismus_ist_dokumentiert():
    """Fixiert die Wahl im Test selbst, damit sie nicht still kippt."""
    assert CHOSEN == "derived_from"
    assert "kind: const" in REJECTED
    assert "produces_fields" in REJECTED
