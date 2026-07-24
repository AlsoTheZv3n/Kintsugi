"""Erzeugt den offline books.toscrape.com-Corpus (I0.9.9).

Kein Live-Abruf: 0.5 rps ueber ~1050 URLs waeren ~35 Minuten und docs/07 warnt,
dass die Sandbox-Seiten verschwinden. Dieser Generator baut eine books.toscrape.
com-**foermige** Struktur, deren HTML exakt auf die vier Pack-Selektoren passt
(``div.product_main > h1``, ``p.price_color``, ``p.availability``,
``table.table-striped tr:nth-child(1) td``), sodass der echte Extraktor >=200
Records zieht.

Bewusst mit Rand: 12 Index-Seiten (page-1 … page-12) und die 240 Detailseiten,
die sie referenzieren. Die DoD-Schwelle ist 200 betrachtete Zeilen inklusiv — ein
Corpus von genau 200 liesse einen einzigen quarantaenierten Datensatz den Lauf
aus DoD-fremdem Grund als ``failed`` schliessen.

Aufruf: ``uv run python tests/generate_books_corpus.py``. Deterministisch; ein
erneuter Lauf auf unveraendertem Baum laesst ``git diff`` leer.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "books.toscrape.com" / "book"
PAGES = 12
PER_PAGE = 20
LAST_MODIFIED = "Mon, 20 Jul 2026 00:00:00 GMT"
CONTENT_TYPE = "text/html"  # bewusst OHNE charset — wie die echte Seite

# Ein langer Nicht-ASCII-Titel fuer die Golden-Kante.
LONG_TITLE = "Årsteruttrykk øwær: naïve Café Résumé Straße 日本語 книга — Band Ω"


def _upc(seed: str) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:16]


def _detail_html(*, title: str, price: str, upc: str, availability: str) -> str:
    # Eine Produktbeschreibung, damit die Seite mehr als 200 Byte sichtbaren Text
    # traegt — sonst flaggt block_detect eine legitime Seite als leere Blockade
    # (empty_below_text_floor). Deterministisch aus dem Titel abgeleitet.
    description = (
        f"{title} is a compelling volume in the Kintsugi offline test corpus. "
        "This synthetic product description exists so the rendered detail page "
        "carries well over two hundred bytes of visible text, keeping the block "
        "detector's empty-page floor satisfied for a genuine content page while "
        "leaving the four extracted selectors untouched."
    )
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>{title} | Books to Scrape</title></head><body>\n"
        '<article class="product_page">\n'
        '<div class="product_main">\n'
        f"<h1>{title}</h1>\n"
        f'<p class="price_color">£{price}</p>\n'
        f'<p class="instock availability"><i class="icon-ok"></i> {availability}</p>\n'
        "</div>\n"
        '<table class="table table-striped">\n'
        f"<tr><th>UPC</th><td>{upc}</td></tr>\n"
        f"<tr><th>Price (excl. tax)</th><td>£{price}</td></tr>\n"
        "</table>\n"
        f'<div id="product_description"><h2>Product Description</h2><p>{description}</p></div>\n'
        "</article>\n</body></html>\n"
    )


def _product_pod(rel_href: str, title: str, price: str) -> str:
    return (
        '<li class="col-xs-6 col-sm-4 col-md-3 col-lg-3">\n'
        '<article class="product_pod">\n'
        f'<h3><a href="{rel_href}" title="{title}">{title}</a></h3>\n'
        f'<div class="product_price"><p class="price_color">£{price}</p>\n'
        '<p class="instock availability"><i class="icon-ok"></i> In stock</p></div>\n'
        "</article></li>\n"
    )


def _index_html(n: int, pods: list[str]) -> str:
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>Books to Scrape - Page {n}</title></head><body>\n"
        '<section><ol class="row">\n' + "".join(pods) + "</ol></section>\n"
        f'<ul class="pager"><li class="current">Page {n} of {PAGES}</li></ul>\n'
        "</body></html>\n"
    )


def _write_blob(corpus: Path, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    (corpus / f"{digest}.html.gz").write_bytes(gzip.compress(body, mtime=0))
    return digest


def _manifest_entry(digest: str) -> dict[str, object]:
    return {
        "blob": f"{digest}.html.gz",
        "http_status": 200,
        "content_type": CONTENT_TYPE,
        "etag": f'"{digest[:16]}"',
        "last_modified": LAST_MODIFIED,
    }


def _write_golden(dirname: str, golden_label: str, body: bytes, meta: dict[str, object]) -> None:
    dest = ROOT / "golden" / dirname
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "page.html.gz").write_bytes(gzip.compress(body, mtime=0))
    digest = hashlib.sha256(body).hexdigest()
    full = {
        "url": meta["url"],
        "fetched_at": "2026-07-20T00:00:00+00:00",
        "http_status": 200,
        "content_type": CONTENT_TYPE,
        "content_hash": digest,
        "byte_size": len(body),
        "fetcher": "httpx",
        "golden_label": golden_label,
        "synthetic": meta.get("synthetic", False),
        "derived_from": meta.get("derived_from"),
        "edit": meta.get("edit"),
    }
    (dest / "meta.json").write_text(
        json.dumps(full, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _write_taxonomy() -> None:
    """23 baseline-Fixtures + die 7 Edge-Klassen (I1.3.3), >=30 Verzeichnisse.

    'baseline' (CssExtractor, eigenes meta-Format) bleibt unangetastet; diese
    FixtureMeta-Fixtures liegen daneben. Jede Edge-Fixture behaelt das echte DOM
    und editiert genau eine Region (synthetic + derived_from + edit).
    """
    from kintsugi.harness.fixtures_cli import label_dirname

    base = "https://books.toscrape.com/catalogue"

    for n in range(1, 24):  # 23 baselines
        _write_golden(
            f"baseline-{n:02d}",
            "baseline",
            _detail_html(
                title=f"Baseline Book {n:02d}",
                price=f"{10 + n}.{n % 100:02d}",
                upc=_upc(f"baseline-{n}"),
                availability=f"In stock ({(n % 20) + 1} available)",
            ).encode(),
            {"url": f"{base}/baseline-book-{n:02d}_{8000 + n}/index.html"},
        )

    def edge(slug: str, body: bytes, edit: str) -> None:
        _write_golden(
            label_dirname(f"edge:{slug}"),
            f"edge:{slug}",
            body,
            {
                "url": f"{base}/edge-{slug.replace('_', '-')}_9000/index.html",
                "synthetic": True,
                "derived_from": f"baseline; {edit}",
                "edit": edit,
            },
        )

    edge(
        "out_of_stock",
        _detail_html(
            title="Out Of Stock Sample",
            price="19.99",
            upc=_upc("out_of_stock"),
            availability="Out of stock",
        ).encode(),
        "availability: 'In stock (22 available)' -> 'Out of stock'",
    )
    edge(
        "missing_optional",
        _detail_html(
            title="No Availability Count",
            price="12.34",
            upc=_upc("missing_optional"),
            availability="In stock",
        ).encode(),  # kein '(N available)' -> int None
        "availability ohne Zahl, optionales Feld null",
    )
    edge(
        "special_chars",
        _detail_html(
            title="Cœur &amp; Ægis: &lt;Über&gt; ½",
            price="7.77",
            upc=_upc("special_chars"),
            availability="In stock (3 available)",
        ).encode(),
        "title mit Sonderzeichen und HTML-Entities",
    )
    edge(
        "very_long_value",
        _detail_html(
            title=LONG_TITLE + " " + "Fortsetzung " * 12,
            price="42.00",
            upc=_upc("very_long_value"),
            availability="In stock (5 available)",
        ).encode(),
        "title auf ueber 200 Zeichen verlaengert",
    )
    edge(
        "very_short_value",
        _detail_html(
            title="A",
            price="1.00",
            upc=_upc("very_short_value"),
            availability="In stock (1 available)",
        ).encode(),
        "title auf ein Zeichen gekuerzt",
    )
    edge(
        "multilingual",
        _detail_html(
            title="日本語 книга café Ærø Straße Ω",
            price="33.33",
            upc=_upc("multilingual"),
            availability="In stock (9 available)",
        ).encode(),
        "title mehrsprachig/Nicht-ASCII",
    )
    edge(
        "zero_results",
        _index_html(99, []).encode(),  # Listenseite ohne Produkte
        "Index-Seite ohne product_pod-Eintraege",
    )


def main() -> None:
    corpus = ROOT / "corpus"
    if corpus.exists():
        shutil.rmtree(corpus)
    corpus.mkdir(parents=True)
    manifest: dict[str, dict[str, object]] = {}

    idx = 0
    for n in range(1, PAGES + 1):
        pods: list[str] = []
        for _ in range(PER_PAGE):
            idx += 1
            slug = f"book-{idx:03d}_{idx}"
            title = f"Book Title {idx:03d}"
            price = f"{10 + (idx % 90)}.{idx % 100:02d}"
            avail = f"In stock ({(idx % 30) + 1} available)"
            upc = _upc(slug)
            body = _detail_html(title=title, price=price, upc=upc, availability=avail).encode()
            digest = _write_blob(corpus, body)
            manifest[f"/catalogue/{slug}/index.html"] = _manifest_entry(digest)
            pods.append(_product_pod(f"{slug}/index.html", title, price))
        index_body = _index_html(n, pods).encode()
        digest = _write_blob(corpus, index_body)
        manifest[f"/catalogue/page-{n}.html"] = _manifest_entry(digest)

    (corpus / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Golden-Fixturebestand mit Edge-Taxonomie (I1.3.3). 'baseline' (CssExtractor,
    # eigenes meta-Format) bleibt daneben unangetastet.
    golden_root = ROOT / "golden"
    for child in sorted(golden_root.iterdir()) if golden_root.is_dir() else []:
        # Alte FixtureMeta-Edges/baselines aufraeumen; die CssExtractor-'baseline'
        # (Key 'label') behalten.
        meta_file = child / "meta.json"
        if child.is_dir() and meta_file.is_file():
            import json as _json

            data = _json.loads(meta_file.read_text(encoding="utf-8"))
            if "golden_label" in data:  # FixtureMeta-Format
                shutil.rmtree(child)
    _write_taxonomy()

    # Regenerierbaren Index schreiben.
    from kintsugi.harness.fixtures_cli import write_index

    write_index(ROOT.parents[1])
    print(f"Corpus: {len(manifest)} Pfade, {PAGES} Index-Seiten, {PAGES * PER_PAGE} Detailseiten.")


if __name__ == "__main__":
    main()
