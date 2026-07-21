"""Compliance-Wache gegen Browser-Tarnung im Quelltext.

README.md, Abschnitt „Compliance", verpflichtet das Projekt auf einen
identifizierbaren User-Agent mit Kontaktadresse. `docs/07-test-targets.md`
schliesst das Wettruesten gegen Bot-Schutz ausdruecklich aus. Eine Regel, die
nur im Dokument steht, wird gebrochen — deshalb steht sie hier als Test.

Der eigentliche Durchsetzungspunkt im Fetcher entsteht spaeter; dieser Test
sichert vorab ab, dass sich kein getarnter User-Agent einschleicht.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Kennzeichen echter Browser. Wer sie in einen User-Agent schreibt, tarnt sich.
SPOOFING_TOKENS = ("Mozilla/", "AppleWebKit", "Chrome/", "Safari/", "Gecko/")

SEARCHED_DIRS = ("kintsugi", "packs", "ops")


def _python_and_config_files():
    for directory in SEARCHED_DIRS:
        base = PROJECT_ROOT / directory
        if not base.exists():
            continue
        for suffix in ("*.py", "*.yaml", "*.yml", "*.toml"):
            yield from base.rglob(suffix)


def test_kein_getarnter_user_agent_im_quelltext():
    """Kein Modul und kein Site-Pack darf einen Browser-User-Agent enthalten."""
    offenders = []
    for path in _python_and_config_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in SPOOFING_TOKENS:
            if token in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {token}")
    assert not offenders, "Browser-Tarnung gefunden:\n" + "\n".join(offenders)


def test_protego_ist_als_robots_parser_deklariert():
    """robots.txt wird mit einem echten Parser ausgewertet, nicht per Regex."""
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert any(d.startswith("protego") for d in deps), (
        "protego fehlt in den Abhaengigkeiten — robots.txt braucht einen "
        "richtigen Parser, siehe README Abschnitt Compliance."
    )


@pytest.mark.parametrize("forbidden", ["selenium", "undetected", "cloudscraper", "fake-useragent"])
def test_keine_bot_schutz_umgehung_als_abhaengigkeit(forbidden):
    """docs/07: kein Wettruesten gegen Bot-Schutz, ausdruecklich ausserhalb des Scopes."""
    raw = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert forbidden not in raw
