"""Zulassungspruefung tools/check_sources.py (I1.5.1, #100).

Offline gegen aufgezeichnete robots-Fixtures (kein Netz, der Socket-Riegel bleibt
scharf). Deckt F1 (404 -> allow-all) und F4 (webscraper.io ist unzulaessig) ab.
"""

from __future__ import annotations

from pathlib import Path

from kintsugi.config import Settings
from tools.check_sources import all_entry_urls, check

_SETTINGS = Settings(contact="ops@example.invalid")


def test_offline_alle_quellen_erlaubt_exit0():
    code, verdicts = check(offline=True, settings=_SETTINGS)
    assert code == 0
    # Ein Urteil je Quelle (books, quotes, scrapethissite).
    labels = {v.label for v in verdicts}
    assert any(label.startswith("books.toscrape.com") for label in labels)
    assert any(label.startswith("quotes.toscrape.com") for label in labels)
    assert any(label.startswith("scrapethissite.com") for label in labels)
    assert all(v.allowed for v in verdicts)


def test_f1_404_robots_ist_allow_all():
    code, verdicts = check(offline=True, settings=_SETTINGS)
    assert code == 0
    books = next(v for v in verdicts if v.label.startswith("books.toscrape.com"))
    assert books.allowed
    assert books.reason == "allowed (no robots.txt, RFC 9309 2.3.1.3)"


def test_f4_injiziertes_webscraper_bricht_mit_disallow_zeile():
    code, verdicts = check(
        offline=True,
        inject=["https://webscraper.io/test-sites/e-commerce/ajax"],
        settings=_SETTINGS,
    )
    assert code != 0
    bad = next(v for v in verdicts if "webscraper.io" in v.url)
    assert not bad.allowed
    assert "Disallow: /test-sites/e-commerce/" in bad.reason


def test_kein_pack_deklariert_eine_webscraper_eintritts_url():
    # AC3 (getrackt; die docs/07+08-Prosa ist gitignored): webscraper.io darf
    # nirgends als Eintritts-URL auftauchen.
    urls = all_entry_urls()
    assert urls  # es gibt ueberhaupt Eintritts-URLs
    assert not any("webscraper.io" in url for url in urls)


def test_compliance_traegt_admission_zeilen_fuer_alle_drei():
    text = Path("COMPLIANCE.md").read_text(encoding="utf-8")
    assert "books.toscrape.com" in text
    assert "quotes.toscrape.com" in text
    assert "scrapethissite.com" in text
    # robots-Verdikt, Datum und der User-Agent-String der Pruefung.
    assert "2026-07-24" in text
    assert "kintsugi/0.1 (+" in text


def test_ci_ruft_die_zulassungspruefung_offline():
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "tools/check_sources.py --offline" in ci
