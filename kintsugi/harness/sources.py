"""Die drei Phase-1-Quellen als slash-freie Registry-Keys (I1.4.3, F4/F5).

Der Mutationskatalog (E1.6) und die Quellen-Packs (E1.5) referenzieren jede
Quelle ueber einen **slash-freien** Key, nie ueber ihre URL — ein Key wird zu
Verzeichnisnamen und Test-ids, und ein ``/`` darin waere unter Windows nicht
darstellbar (siehe die golden-``label_dirname``-Regel). Die Eintritts-URL steht
deshalb als eigenes Attribut daneben.

- ``books`` — books.toscrape.com (F1: Pagination endet mit echtem HTTP 404).
- ``quotes_js`` — quotes.toscrape.com/js (F5: keine ``.quote``-Elemente, die
  Daten liegen in ``var data = [...]`` und werden ueber den embedded_json-
  inline-js-var-Modus geholt).
- ``scrapethissite_ajax`` — scrapethissite.com/pages/ajax-javascript (F4:
  Ersatz fuer webscraper.io/test-sites/e-commerce/ajax, dessen robots.txt
  ``/test-sites/e-commerce/`` verbietet; die Daten kommen per XHR-Endpunkt).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PHASE1_SOURCES", "Phase1Source"]


@dataclass(frozen=True)
class Phase1Source:
    """Eine Phase-1-Quelle: slash-freier Key, Domain und Eintritts-URL getrennt."""

    key: str
    domain: str
    entry_url: str


PHASE1_SOURCES: dict[str, Phase1Source] = {
    "books": Phase1Source(
        key="books",
        domain="books.toscrape.com",
        entry_url="https://books.toscrape.com/",
    ),
    "quotes_js": Phase1Source(
        key="quotes_js",
        domain="quotes.toscrape.com",
        entry_url="https://quotes.toscrape.com/js/",
    ),
    "scrapethissite_ajax": Phase1Source(
        key="scrapethissite_ajax",
        domain="www.scrapethissite.com",
        entry_url="https://www.scrapethissite.com/pages/ajax-javascript/",
    ),
}
