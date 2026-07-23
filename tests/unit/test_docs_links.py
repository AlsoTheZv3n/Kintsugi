"""Haelt die getrackten Dokumente ehrlich: keine toten Links, keine Altlasten.

Sechs Verweise auf der Startseite zeigten einmal auf Dateien, die es nicht gab
(LICENSE, COMPLIANCE.md, .github/workflows/ci.yml, pyproject.toml,
frontend/package.json, docs/demo.gif). Dieser Test faengt genau das: jeder
relative Link in den getrackten Dokumenten muss auf eine existierende Datei
zeigen, und eine Reihe von Stack-/Zahl-Altlasten darf nicht zurueckkehren.

Die Dokumentmenge ist README.md und COMPLIANCE.md — die getrackten Docs. Die
Entwurfsdokumente unter docs/ sind lokal und in CI nicht vorhanden; sie gehoeren
deshalb nicht in eine Pruefung, die auf dem Runner gruen sein muss.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Getrackte Dokumente. docs/*.md ist bewusst NICHT dabei (lokal, nicht in CI).
DOC_SET = [PROJECT_ROOT / "README.md", PROJECT_ROOT / "COMPLIANCE.md"]

# Markdown-Link und -Image: [text](ziel) bzw. ![alt](ziel).
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")

# Strings, die einen frueheren Widerspruch markieren und nicht zurueckkehren
# duerfen. Die Liste lebt hier, und die Pruefung ist auf DOC_SET beschraenkt,
# damit der Test sich nicht selbst meldet.
BANNED = ["3.14", "PostgreSQL 18", "Postgres 18", "React 19", "No Next.js", "frontend/", "Siebzehn"]


def _relative_link_targets(text: str) -> list[str]:
    targets = []
    for raw in LINK_RE.findall(text):
        target = raw.strip()
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#", 1)[0]  # #anchor abschneiden
        if target:
            targets.append(target)
    return targets


@pytest.mark.parametrize("doc", DOC_SET, ids=lambda p: p.name)
def test_jeder_relative_link_existiert(doc: Path):
    text = doc.read_text(encoding="utf-8")
    tot = []
    for target in _relative_link_targets(text):
        if not (PROJECT_ROOT / target).exists():
            tot.append(target)
    assert not tot, f"{doc.name} verweist auf nicht existierende Pfade: {tot}"


@pytest.mark.parametrize("doc", DOC_SET, ids=lambda p: p.name)
def test_keine_verbotenen_altlasten(doc: Path):
    text = doc.read_text(encoding="utf-8")
    found = [needle for needle in BANNED if needle in text]
    assert not found, f"{doc.name} enthaelt verbotene Altlast(en): {found}"


def test_der_test_findet_ueberhaupt_links():
    """Sonst bestuende test_jeder_relative_link_existiert stillschweigend."""
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert _relative_link_targets(readme), "keine relativen Links in README gefunden"


def test_pruefung_ist_auf_das_docset_beschraenkt():
    """Dieses Modul enthaelt die verbotenen Strings (in BANNED), muss aber gruen
    bleiben — weil die Pruefung ausschliesslich DOC_SET liest, nicht sich selbst.
    """
    this_file = Path(__file__)
    assert this_file not in DOC_SET
    # Beweis, dass die Strings hier tatsaechlich vorkommen und der Scan sie
    # dennoch nicht faengt:
    assert "Siebzehn" in this_file.read_text(encoding="utf-8")
