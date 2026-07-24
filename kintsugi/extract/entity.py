"""Eine oder mehrere Entitaeten aus einem Dokument zusammensetzen.

Bindeglied zwischen der Prioritaetskette (roh je Feld) und der zeilenweisen
Validierung. Je Quelle werden die Rohwerte gezogen und **sofort** durch die
Feld-Transform-Kette geschickt (``[strip, parse_currency]``), dann je Feld
first-non-empty gemischt und zuletzt ``derived_from`` angewandt (currency aus dem
Money von price, das danach auf seinen Betrag ausgepackt wird). Das Ergebnis ist
genau das, was ``validate_row`` erwartet.

Zwei Sichten auf dieselbe Seite:

- ``extract_entity`` — die **erste** Entitaet (books: eine je Detailseite).
- ``extract_entities`` — **alle** Entitaeten (quotes/scrapethissite: N je Seite).

Beide teilen sich ``_assemble`` (Transform -> Mischen -> derive fuer eine Zeile);
der einzige Unterschied ist, ob je Quelle ``extract`` (die erste Zeile) oder
``extract_all`` (alle Zeilen) gezogen wird.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kintsugi.extract.base import resolve as resolve_extractor
from kintsugi.extract.derive import apply_derived_fields
from kintsugi.transform.registry import apply_transforms

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from kintsugi.extract.base import Extractor
    from kintsugi.packs.model import SitePack, SourceSpec

__all__ = ["extract_entities", "extract_entity"]

_Row = dict[str, object]
_Entity = tuple[dict[str, object], dict[str, str]]


def _empty(value: object) -> bool:
    return value is None or value == ""


def _resolve(kind: str, registry: Mapping[str, Extractor] | None) -> Extractor:
    """Der Extraktor zur Art — global oder aus einer eingespeisten Registry (Test)."""
    return resolve_extractor(kind) if registry is None else registry[kind]


def _assemble(pack: SitePack, items: Sequence[tuple[SourceSpec, _Row | None]]) -> _Entity:
    """Setzt aus den Rohwerten je Quelle **eine** Entitaet zusammen.

    ``items`` paart jede Quelle mit ihrem Rohwert-dict fuer genau diese Zeile
    (``None``, wenn die Quelle an dieser Zeilenposition nichts lieferte — eine
    Mehrzeilen-Quelle, die weniger Zeilen hat als die zeilensetzende). Je Feld
    wird transformiert, first-non-empty ueber die Quellen gemischt und zuletzt
    ``derived_from`` angewandt — die Reihenfolge, die ``validate_row`` erwartet.
    Neben den Werten faellt eine Provenance ab (welche Art welches Feld gewann);
    „CSS gewann, wo frueher JSON-LD gewann" ist fuer sich ein Bruchsignal
    (docs/03 §Laeufe).
    """
    values: dict[str, object] = {}
    provenance: dict[str, str] = {}
    for source, raw in items:
        if raw is None:
            continue
        # Feld-Specs je Quellen-Art unterschiedlich benannt; ueber getattr, damit
        # die Nicht-CSS-Quellen hier nicht per Typ stolpern.
        field_specs = getattr(source, "fields", {})
        for name, extracted in raw.items():
            value: object = extracted
            spec = field_specs.get(name) if isinstance(field_specs, dict) else None
            transforms = getattr(spec, "transform", None)
            if value is not None and transforms:
                value = apply_transforms(transforms, value)
            if _empty(values.get(name)) and not _empty(value):
                values[name] = value
                provenance[name] = source.kind
            values.setdefault(name, None)
    return apply_derived_fields(values, pack.schema_.fields), provenance


def extract_entity(
    pack: SitePack, doc: object, *, registry: Mapping[str, Extractor] | None = None
) -> _Entity:
    """Liefert ``(values, provenance)`` fuer die **erste** Entitaet der Seite.

    Die Einzeilen-Sicht der Prioritaetskette: je Quelle den ersten Treffer ziehen
    und mischen. Fuer Mehrzeilen-Seiten liefert ``extract_entities`` alle N.
    """
    items = [(s, _resolve(s.kind, registry).extract(doc, s)) for s in pack.extract.sources]
    return _assemble(pack, items)


def extract_entities(
    pack: SitePack, doc: object, *, registry: Mapping[str, Extractor] | None = None
) -> list[_Entity]:
    """Liefert je Entitaet der Seite ein ``(values, provenance)`` (Mehrzeilen).

    Zeilen werden **positionsweise** ueber die Quellen ausgerichtet: die erste
    Quelle mit mindestens einer Zeile bestimmt die Zeilenmenge N — die treue
    Verallgemeinerung von „erster Treffer gewinnt je Feld" auf „erste Quelle
    gewinnt die Zeilenmenge". Spaetere Quellen fuellen an Position ``i`` nur noch
    leere Felder; sie ergaenzen, verlaengern nie. Das ist genau die Fallback-Rolle,
    die #104 (embedded_json, dann css) und #105 (xhr, dann css) der Sekundaerquelle
    geben: liefert die Primaerquelle die Zeilen, traegt der css-Fallback auf ``/js/``
    bzw. der leeren AJAX-Tabelle nichts bei; faellt die Primaerquelle aus (null
    Zeilen), uebernimmt die naechste Quelle die Zeilenmenge.

    Eine Seite ohne Zeilen liefert ``[]`` — keine Entitaet (etwa eine leere
    Listenseite jenseits der letzten). Der Aufrufer (Runner/Replay) faechert
    darueber auf: N Validierungen, N Records, per-Entitaet-Zaehler.
    """
    per_source = [(s, _resolve(s.kind, registry).extract_all(doc, s)) for s in pack.extract.sources]
    n = next((len(rows) for _, rows in per_source if rows), 0)
    results: list[_Entity] = []
    for i in range(n):
        items = [(s, rows[i] if i < len(rows) else None) for s, rows in per_source]
        results.append(_assemble(pack, items))
    return results
