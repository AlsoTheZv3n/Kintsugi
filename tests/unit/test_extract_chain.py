"""Prueft das Extractor-Protokoll, die Prioritaetskette und derive (I0.8.1)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from kintsugi.extract.base import PackConfigError, resolve
from kintsugi.extract.chain import run_chain
from kintsugi.extract.derive import apply_derived_fields
from kintsugi.packs.model import FieldSchema
from kintsugi.transform.primitives import Money


class _Fixed:
    """Test-Extraktor, der ein festes dict liefert."""

    def __init__(self, produced: dict[str, str | None]) -> None:
        self._produced = produced

    def extract(self, doc: object, source: object) -> dict[str, str | None]:
        return dict(self._produced)


def test_first_non_empty_gewinnt_je_feld():
    sources = [SimpleNamespace(kind="a"), SimpleNamespace(kind="b")]
    registry = {
        "a": _Fixed({"title": "X", "upc": None}),
        "b": _Fixed({"upc": "abc"}),
    }
    values, provenance = run_chain(sources, doc=None, registry=registry)
    assert values == {"title": "X", "upc": "abc"}
    # Provenance je Feld, nicht je Lauf.
    assert provenance == {"title": "a", "upc": "b"}


def test_leerstring_zaehlt_als_leer():
    sources = [SimpleNamespace(kind="a"), SimpleNamespace(kind="b")]
    registry = {"a": _Fixed({"title": ""}), "b": _Fixed({"title": "Y"})}
    values, provenance = run_chain(sources, doc=None, registry=registry)
    assert values["title"] == "Y"
    assert provenance["title"] == "b"


@pytest.mark.parametrize(
    ("kind", "phase"),
    [
        # embedded_json ist ab I1.5.2 gebaut und daher kein Stub mehr.
        ("jsonld", "Phase 1"),
        ("xhr", "Phase 1"),
        ("llm", "Phase 4"),
        ("api", "Phase 5"),
    ],
)
def test_stub_extraktor_nennt_seine_phase(kind, phase):
    with pytest.raises(NotImplementedError, match=phase):
        resolve(kind).extract(doc=None, source=None)


def test_unbekannte_art_ist_konfigurationsfehler():
    with pytest.raises(PackConfigError, match="unbekannt"):
        resolve("voellig_unbekannt")


def test_unbekannte_art_ist_kein_keyerror_oder_notimplemented():
    with pytest.raises(PackConfigError):
        run_chain([SimpleNamespace(kind="wibble")], doc=None)


def test_derive_erzeugt_currency_und_packt_money_aus():
    """ADR-013: ein parse_currency-Ergebnis liefert price UND currency."""
    values = {"price": Money(Decimal("51.77"), "GBP")}
    schema = {
        "price": FieldSchema(type="decimal", required=True),
        "currency": FieldSchema.model_validate(
            {
                "type": "string",
                "required": True,
                "derived_from": {"source": "price", "transform": "currency_from_symbol"},
            }
        ),
    }
    result = apply_derived_fields(values, schema)
    assert result["currency"] == "GBP"
    assert result["price"] == Decimal("51.77")
    assert not isinstance(result["price"], Money)
