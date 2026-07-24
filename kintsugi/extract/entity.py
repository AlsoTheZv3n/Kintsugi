"""Eine Entitaet aus einem Dokument zusammensetzen: extrahieren, transformieren, ableiten.

Bindeglied zwischen der Prioritaetskette (``run_chain``, roh je Feld) und der
zeilenweisen Validierung. Je Quelle werden die Rohwerte gezogen und **sofort**
durch die Feld-Transform-Kette geschickt (``[strip, parse_currency]``), dann je
Feld first-non-empty gemischt und zuletzt ``derived_from`` angewandt (currency
aus dem Money von price, das danach auf seinen Betrag ausgepackt wird). Das
Ergebnis ist genau das, was ``validate_row`` erwartet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kintsugi.extract.base import resolve as resolve_extractor
from kintsugi.extract.derive import apply_derived_fields
from kintsugi.transform.registry import apply_transforms

if TYPE_CHECKING:
    from kintsugi.packs.model import SitePack

__all__ = ["extract_entity"]


def _empty(value: object) -> bool:
    return value is None or value == ""


def extract_entity(pack: SitePack, doc: object) -> tuple[dict[str, object], dict[str, str]]:
    """Liefert ``(values, provenance)`` fuer eine Entitaet.

    ``values`` ist bereit fuer ``validate_row``; ``provenance`` nennt je Feld die
    Quellen-Art, die es gewonnen hat (docs/03 §Laeufe: „CSS gewann, wo frueher
    JSON-LD gewann" ist ein Bruchsignal).
    """
    values: dict[str, object] = {}
    provenance: dict[str, str] = {}
    for source in pack.extract.sources:
        extractor = resolve_extractor(source.kind)
        raw = extractor.extract(doc, source)
        # Feld-Specs je nach Quellen-Art unterschiedlich benannt; ueber getattr,
        # damit die Nicht-CSS-Quellen (Phase 1+) hier nicht per Typ stolpern.
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
    derived = apply_derived_fields(values, pack.schema_.fields)
    return derived, provenance
