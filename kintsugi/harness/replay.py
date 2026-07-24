"""Replay-Harness: Site-Pack gegen Golden-Fixtures, feldweiser Vergleich (I1.3.5).

docs/08 §Phase 1 („Replay-Harness … feldweiser Vergleich") und docs/04
§Freigabe-Gate („Ein einziger abweichender Wert ist ein Durchfall"). ``replay``
faehrt Extraktion und Transform ueber die dekomprimierten Fixture-Koerper —
**kein Netz, keine Datenbank**, kein Fetcher, keine Session, keine Engine.

Gate-Regel exakt wie docs/04: bestanden verlangt, dass **jedes Pflichtfeld** auf
**jeder** Fixture exakt seinem ``expected.json``-Wert entspricht und die
Non-Null-Zahl **jedes optionalen Felds** ueber dem Corpus ``>=`` seinem
``baseline.json``-Niveau liegt. Ein einziger abweichender Pflichtwert reisst den
ganzen Report. ``ReplayReport`` ist serialisierbar — dasselbe Objekt konsumieren
das Phase-2-Gate und die Phase-4-Workbench.

``Corpus`` dekomprimiert und parst jede Fixture genau einmal je Session (Zaehler
``parse_count``), damit ein 30-Fixture-Replay unter docs/06' Zwei-Sekunden-Zusage
bleibt und der Mutations-Harness (E1.6) ihn 22-mal je Quelle rufen kann.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict
from selectolax.lexbor import LexborHTMLParser

from kintsugi.extract.entity import extract_entity
from kintsugi.harness.expected_model import ExpectedFixture
from kintsugi.validate.dynamic_model import validate_row

if TYPE_CHECKING:
    from kintsugi.packs.model import SitePack

__all__ = ["Corpus", "FieldResult", "FixtureResult", "ReplayReport", "replay"]

_Json = str | int | float | bool | None


def _json_val(value: object) -> _Json:
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@dataclass
class _Fixture:
    label: str
    directory: Path
    expected: ExpectedFixture


class Corpus:
    """Golden-Fixtures eines (domain, entity) mit Parse-Cache."""

    def __init__(self, root: Path, domain: str, entity: str) -> None:
        self._golden = root / domain / entity / "golden"
        self._baseline = json.loads((root / domain / entity / "baseline.json").read_text("utf-8"))
        self._trees: dict[Path, LexborHTMLParser] = {}
        self.parse_count = 0

    def fixtures(self) -> list[_Fixture]:
        out: list[_Fixture] = []
        for meta_path in sorted(self._golden.rglob("meta.json")):
            data = json.loads(meta_path.read_text("utf-8"))
            if "golden_label" not in data:  # Fremdformat (CssExtractor-Baseline)
                continue
            expected = ExpectedFixture.model_validate_json(
                (meta_path.parent / "expected.json").read_text("utf-8")
            )
            out.append(_Fixture(data["golden_label"], meta_path.parent, expected))
        return out

    def tree(self, fixture: _Fixture) -> LexborHTMLParser:
        cached = self._trees.get(fixture.directory)
        if cached is None:
            body = gzip.decompress((fixture.directory / "page.html.gz").read_bytes())
            cached = LexborHTMLParser(body.decode("utf-8"))
            self._trees[fixture.directory] = cached
            self.parse_count += 1
        return cached

    @property
    def baseline(self) -> dict[str, int]:
        return dict(self._baseline.get("optional_nonnull", {}))


class FieldResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    expected: _Json
    actual: _Json
    ok: bool
    required: bool


class FixtureResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    fields: list[FieldResult]
    row_count_ok: bool
    natural_keys_ok: bool


class ReplayReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixtures: list[FixtureResult]
    optional_counts: dict[str, int]
    baseline: dict[str, int]
    passed: bool


def replay(pack: SitePack, corpus: Corpus) -> ReplayReport:
    schema = pack.schema_
    optional = [name for name, fs in schema.fields.items() if not fs.required]
    optional_counts = dict.fromkeys(optional, 0)
    results: list[FixtureResult] = []
    passed = True

    for fixture in corpus.fixtures():
        tree = corpus.tree(fixture)
        values, _ = extract_entity(pack, tree)
        validated = validate_row(pack, values)

        if fixture.expected.expected_row_count == 0:
            # Listen-/Nullseite: es darf keine gueltige Entitaet herausfallen.
            row_ok = not validated.accepted
            results.append(
                FixtureResult(
                    label=fixture.label, fields=[], row_count_ok=row_ok, natural_keys_ok=True
                )
            )
            passed = passed and row_ok
            continue

        field_results: list[FieldResult] = []
        for name, expected_field in fixture.expected.fields.items():
            actual = _json_val(values.get(name))
            ok = actual == expected_field.value
            field_results.append(
                FieldResult(
                    field=name,
                    expected=expected_field.value,
                    actual=actual,
                    ok=ok,
                    required=expected_field.required,
                )
            )
            if expected_field.required and not ok:
                passed = False
            if not expected_field.required and actual is not None:
                optional_counts[name] += 1

        key = _json_val(values.get(schema.natural_key[0])) if schema.natural_key else None
        natural_keys_ok = [key] == fixture.expected.expected_natural_keys
        if not natural_keys_ok:
            passed = False
        results.append(
            FixtureResult(
                label=fixture.label,
                fields=field_results,
                row_count_ok=validated.accepted,
                natural_keys_ok=natural_keys_ok,
            )
        )
        if not validated.accepted:
            passed = False

    for name in optional:
        if optional_counts[name] < corpus.baseline.get(name, 0):
            passed = False

    return ReplayReport(
        fixtures=results,
        optional_counts=optional_counts,
        baseline=corpus.baseline,
        passed=passed,
    )
