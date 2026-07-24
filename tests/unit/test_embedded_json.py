"""embedded_json: script_id, inline_js_var (F5), Decoy und abgeschnittenes Literal (I1.5.2)."""

from __future__ import annotations

import json

import pytest
from kintsugi.extract.embedded_json import EmbeddedJsonError, EmbeddedJsonExtractor
from kintsugi.packs.model import EmbeddedJsonSource
from selectolax.lexbor import LexborHTMLParser

_EX = EmbeddedJsonExtractor()


def _doc(body: str) -> LexborHTMLParser:
    return LexborHTMLParser(f"<html><body>{body}</body></html>")


def test_script_id_modus_ueber_root():
    payload = {
        "props": {"pageProps": {"product": {"title": "A Light in the Attic", "price": "51.77"}}}
    }
    doc = _doc(f'<script id="__NEXT_DATA__">{json.dumps(payload)}</script>')
    source = EmbeddedJsonSource(
        kind="embedded_json", script_id="__NEXT_DATA__", root="props.pageProps.product"
    )
    assert _EX.extract(doc, source) == {"title": "A Light in the Attic", "price": "51.77"}


def test_inline_js_var_liefert_zehn_objekte():
    # F5: quotes.toscrape.com/js legt die Daten als `var data = [...]` ohne id ab.
    data = [
        {
            "text": f"Zitat {i}",
            "author": {"name": f"Autor {i}", "slug": f"autor-{i}"},
            "tags": ["t1", "t2"],
        }
        for i in range(10)
    ]
    doc = _doc(f"<script>var data = {json.dumps(data)};</script>")
    source = EmbeddedJsonSource(kind="embedded_json", var_name="data")
    rows = _EX.extract_all(doc, source)
    assert len(rows) == 10
    for row in rows:
        assert set(row) >= {"text", "author", "tags"}
        assert isinstance(row["tags"], list)
    assert rows[0]["text"] == "Zitat 0"


def test_extract_liefert_erste_entitaet():
    data = [{"text": "eins"}, {"text": "zwei"}]
    doc = _doc(f"<script>window.data = {json.dumps(data)}</script>")
    source = EmbeddedJsonSource(kind="embedded_json", var_name="data")
    assert _EX.extract(doc, source) == {"text": "eins"}


def test_decoy_name_im_string_matcht_nicht():
    # Der Name taucht nur in einem String auf, nicht als Zuweisung -> kein Treffer.
    doc = _doc('<script>var label = "hier stehen data-Zitate";</script>')
    source = EmbeddedJsonSource(kind="embedded_json", var_name="data")
    with pytest.raises(EmbeddedJsonError, match="keine Zuweisung"):
        _EX.extract_all(doc, source)


def test_abgeschnittenes_literal_wirft_typisierten_fehler():
    doc = _doc('<script>var data = [ {"text": "abc"</script>')  # unbalanciert
    source = EmbeddedJsonSource(kind="embedded_json", var_name="data")
    with pytest.raises(EmbeddedJsonError):
        _EX.extract_all(doc, source)


def test_klammern_in_strings_zaehlen_nicht():
    # Ein `]` in einem String darf den Array-Scan nicht vorzeitig beenden.
    doc = _doc('<script>var data = [{"text": "a ] ] ]", "n": 1}];</script>')
    source = EmbeddedJsonSource(kind="embedded_json", var_name="data")
    rows = _EX.extract_all(doc, source)
    assert rows == [{"text": "a ] ] ]", "n": 1}]


def test_fields_map_bildet_verschachteltes_objekt_ab():
    # #104: author ist ein verschachteltes Objekt, nicht ein String.
    data = [
        {"text": "Zitat A", "author": {"name": "Autor A", "slug": "autor-a"}, "tags": ["x", "y"]},
        {"text": "Zitat B", "author": {"name": "Autor B", "slug": "autor-b"}, "tags": []},
    ]
    doc = _doc(f"<script>var data = {json.dumps(data)};</script>")
    source = EmbeddedJsonSource(
        kind="embedded_json",
        var_name="data",
        fields={
            "text": "$.text",
            "author": "author.name",  # fuehrendes $ ist optional
            "author_slug": "author.slug",
            "tags": "tags",
        },
    )
    rows = _EX.extract_all(doc, source)
    assert rows[0] == {
        "text": "Zitat A",
        "author": "Autor A",
        "author_slug": "autor-a",
        "tags": ["x", "y"],
    }
    # Verschachtelter Name statt des ganzen Objekts; leere tags-Liste bleibt [].
    assert rows[1]["author"] == "Autor B"
    assert rows[1]["tags"] == []


def test_fields_map_extract_liefert_erste_gemappte_zeile():
    data = [{"author": {"name": "Erster"}}, {"author": {"name": "Zweiter"}}]
    doc = _doc(f"<script>var data = {json.dumps(data)};</script>")
    source = EmbeddedJsonSource(
        kind="embedded_json", var_name="data", fields={"author": "author.name"}
    )
    assert _EX.extract(doc, source) == {"author": "Erster"}


def test_fehltreffer_in_fields_map_laesst_feld_weg():
    # Ein Pfad ohne Treffer erzeugt keinen None-Wert, das Feld faellt weg
    # (Fill-Rate-Signal, kein Fehler) — genau wie ein leerer css-Selektor.
    data = [{"text": "nur Text"}]
    doc = _doc(f"<script>var data = {json.dumps(data)};</script>")
    source = EmbeddedJsonSource(
        kind="embedded_json", var_name="data", fields={"text": "$.text", "author": "author.name"}
    )
    assert _EX.extract_all(doc, source) == [{"text": "nur Text"}]
