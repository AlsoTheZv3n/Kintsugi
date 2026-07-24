"""Erzeugt den offline quotes.toscrape.com/js-Corpus (I1.5.6, #106).

Kein Live-Abruf (docs/07 warnt, dass die Sandbox-Seiten verschwinden). Wie der
books-Corpus (``generate_books_corpus.py``) baut dieser Generator eine
``quotes.toscrape.com/js``-**foermige** Struktur: jede Golden-Fixture ist eine
/js/-Seite mit ``var data = [ … ]`` (mehrere Zitate je Seite, F5), keine
``div.quote``-Elemente — genau die Form, aus der der embedded_json-Extraktor N
Entitaeten zieht und der css-Fallback bewusst leer laeuft.

30 Fixtures: 23 Basisfaelle plus 7 Randklassen (docs/07 §Stufe 0: identische
Normalfaelle beweisen nichts). Jede Seite traegt >=2 Zitate, damit die
Mehrzeilen-Replay-Kante (N Entitaeten/Seite) echt geprueft wird; ``edge:zero_results``
ist die leere Seite jenseits der letzten (``var data = []`` -> null Entitaeten).

Aufruf: ``uv run python tests/generate_quotes_corpus.py``. Deterministisch; ein
erneuter Lauf auf unveraendertem Baum laesst ``git diff`` leer.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "quotes.toscrape.com" / "quote"
BASE_URL = "https://quotes.toscrape.com/js"


def _quote(text: str, name: str, slug: str, tags: list[str]) -> dict[str, object]:
    return {
        "text": text,
        "author": {"name": name, "goodreads_link": f"/author/{slug}", "slug": slug},
        "tags": tags,
    }


def _js_page(quotes: list[dict[str, object]]) -> str:
    """Eine /js/-Seite: die Zitate liegen als ``var data`` auf einem script-Tag."""
    data = json.dumps(quotes, ensure_ascii=False)
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        "<title>Quotes to Scrape</title></head><body>\n"
        '<div class="container"><div class="row"><div class="col-md-8">\n'
        f"<script>var data = {data};\nfor (var i in data) {{ /* render */ }}</script>\n"
        "</div></div></div>\n</body></html>\n"
    )


def _write_golden(dirname: str, golden_label: str, body: bytes, meta: dict[str, object]) -> None:
    dest = ROOT / "golden" / dirname
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "page.html.gz").write_bytes(gzip.compress(body, mtime=0))
    full = {
        "url": meta["url"],
        "fetched_at": "2026-07-24T00:00:00+00:00",
        "http_status": 200,
        "content_type": "text/html",  # wie die echte Seite, ohne charset
        "content_hash": hashlib.sha256(body).hexdigest(),
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
    """23 Basis-Fixtures (je 2 Zitate) + 7 Randklassen = 30 Verzeichnisse."""
    for n in range(1, 24):  # 23 baselines
        quotes = [
            _quote(
                f"Zitat {n:02d}-A ueber das Leben und die Wahrheit.",
                f"Autor {n:02d} Alpha",
                f"autor-{n:02d}-alpha",
                ["leben", "wahrheit"],
            ),
            _quote(
                f"Zitat {n:02d}-B ueber Mut und Zeit.",
                f"Autor {n:02d} Beta",
                f"autor-{n:02d}-beta",
                ["mut"],
            ),
        ]
        _write_golden(
            f"baseline-{n:02d}",
            "baseline",
            _js_page(quotes).encode(),
            {"url": f"{BASE_URL}/page/{n}/"},
        )

    def edge(slug: str, quotes: list[dict[str, object]], edit: str) -> None:
        _write_golden(
            f"edge__{slug}",  # ':' ist unter Windows kein Pfadzeichen (label_dirname)
            f"edge:{slug}",
            _js_page(quotes).encode(),
            {
                "url": f"{BASE_URL}/edge/{slug.replace('_', '-')}/",
                "synthetic": True,
                "derived_from": f"baseline; {edit}",
                "edit": edit,
            },
        )

    edge(
        "no_tags",
        [
            _quote("Ein Zitat ganz ohne Schlagworte.", "Ohne Tag", "ohne-tag", []),
            _quote("Noch eines, ebenfalls ohne Tags.", "Leer Tag", "leer-tag", []),
        ],
        "tags-Liste leer (no-tag-Zeile)",
    )
    edge(
        "multi_tags",
        [
            _quote(
                "Ein Zitat mit vielen Schlagworten.",
                "Viel Tag",
                "viel-tag",
                ["leben", "wahrheit", "mut", "zeit", "hoffnung"],
            ),
            _quote("Ein zweites, kurzes.", "Kurz Tag", "kurz-tag", ["kurz"]),
        ],
        "tags-Liste mit fuenf Eintraegen (multi-tag-Zeile)",
    )
    edge(
        "special_chars",
        [
            _quote("Cœur & Ægis: <Über> ½ — „Zitat".replace('"', ""), "Sœur Ç", "soeur-c", ["x"]),
            _quote("Zeichen & Entitäten <b> test.", "Autor Zwei", "autor-zwei", ["y"]),
        ],
        "Sonderzeichen und Entitaeten in Text/Autor",
    )
    edge(
        "very_long_value",
        [
            _quote("Sehr langes Zitat. " * 20, "Lang Autor", "lang-autor", ["lang"]),
            _quote("Zweites, normales Zitat.", "Norm Autor", "norm-autor", ["norm"]),
        ],
        "text auf ueber 300 Zeichen verlaengert",
    )
    edge(
        "very_short_value",
        [
            _quote("A", "K", "k-autor", ["s"]),
            _quote("B", "L", "l-autor", ["t"]),
        ],
        "text auf ein Zeichen gekuerzt",
    )
    edge(
        "multilingual",
        [
            _quote(
                "日本語の名言 café Ærø Straße Ω книга", "Ünïcode Autör", "unicode-autor", ["intl"]
            ),
            _quote(" Второе многоязычное высказывание.", "Кириллица Автор", "kyrillica", ["ru"]),
        ],
        "mehrsprachiger/Nicht-ASCII-Text und -Autor",
    )
    edge(
        "zero_results",
        [],  # var data = [] -> keine Entitaet (leere Seite jenseits der letzten)
        "Seite ohne Zitate (var data leer)",
    )


def _json_val(value: object) -> object:
    from decimal import Decimal

    if isinstance(value, Decimal):
        return str(value)
    return value


def _write_expected_and_baseline() -> None:
    """expected.json (rows je Entitaet) + baseline.json der optionalen Fuellstaende."""
    from kintsugi.extract.entity import extract_entities
    from kintsugi.packs.loader import load_pack
    from selectolax.lexbor import LexborHTMLParser

    pack = load_pack("quotes.toscrape.com", "quote", root=Path("packs"))
    schema_fields = pack.schema_.fields
    key_field = pack.schema_.natural_key[0]
    optional = [name for name, fs in schema_fields.items() if not fs.required]
    optional_nonnull = dict.fromkeys(optional, 0)
    corpus_size = 0

    for meta_path in sorted((ROOT / "golden").rglob("meta.json")):
        body = gzip.decompress((meta_path.parent / "page.html.gz").read_bytes()).decode("utf-8")
        entities = extract_entities(pack, LexborHTMLParser(body))
        rows: list[dict[str, object]] = []
        keys: list[object] = []
        for values, _ in entities:
            rows.append(
                {
                    name: {"value": _json_val(values.get(name)), "required": fs.required}
                    for name, fs in schema_fields.items()
                }
            )
            keys.append(_json_val(values.get(key_field)))
            corpus_size += 1
            for name in optional:
                if values.get(name) is not None:
                    optional_nonnull[name] += 1
        expected: dict[str, object] = {
            "fields": {},
            "rows": rows,
            "expected_row_count": len(entities),
            "expected_natural_keys": keys,
        }
        (meta_path.parent / "expected.json").write_text(
            json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    baseline = {
        "corpus_size": corpus_size,
        "optional_nonnull": dict(sorted(optional_nonnull.items())),
    }
    (ROOT / "baseline.json").write_text(
        json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    golden_root = ROOT / "golden"
    if golden_root.exists():
        shutil.rmtree(golden_root)
    _write_taxonomy()
    _write_expected_and_baseline()

    from kintsugi.harness.fixtures_cli import write_index

    write_index(ROOT.parents[1])
    count = sum(1 for _ in (ROOT / "golden").iterdir())
    print(f"Quotes-Corpus: {count} Golden-Fixtures.")


if __name__ == "__main__":
    main()
