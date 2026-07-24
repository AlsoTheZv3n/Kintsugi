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
        "</table>\n</article>\n</body></html>\n"
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


def _write_golden(label: str, body: bytes, meta: dict[str, object]) -> None:
    from kintsugi.harness.fixtures_cli import label_dirname

    dest = ROOT / "golden" / label_dirname(label)
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
        "golden_label": label,
        "synthetic": meta.get("synthetic", False),
        "derived_from": meta.get("derived_from"),
        "edit": meta.get("edit"),
    }
    (dest / "meta.json").write_text(
        json.dumps(full, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
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

    # Golden-Kanten (ausserhalb des page-{n}-Walks, sie zaehlen also nie mit).
    # 'baseline' gehoert dem CssExtractor-Test (I0.8.1, eigenes meta-Format mit
    # 'expected') und wird hier bewusst NICHT ueberschrieben.
    base = "https://books.toscrape.com/catalogue"
    _write_golden(
        "edge:long_title",
        _detail_html(
            title=LONG_TITLE,
            price="42.00",
            upc=_upc("long_title"),
            availability="In stock (5 available)",
        ).encode(),
        {"url": f"{base}/edge-long-title_9001/index.html"},
    )
    _write_golden(
        "edge:out_of_stock",
        _detail_html(
            title="Out Of Stock Sample",
            price="19.99",
            upc=_upc("out_of_stock"),
            availability="Out of stock",
        ).encode(),
        {
            "url": f"{base}/edge-out-of-stock_9002/index.html",
            "synthetic": True,
            "derived_from": "baseline; availability-Element auf 'Out of stock' editiert",
            "edit": "availability: 'In stock (22 available)' -> 'Out of stock'",
        },
    )
    _write_golden(
        "edge:empty_index",
        _index_html(99, []).encode(),
        {"url": f"{base}/page-empty.html"},
    )

    # Regenerierbaren Index schreiben.
    from kintsugi.harness.fixtures_cli import write_index

    write_index(ROOT.parents[1])
    print(f"Corpus: {len(manifest)} Pfade, {PAGES} Index-Seiten, {PAGES * PER_PAGE} Detailseiten.")


if __name__ == "__main__":
    main()
