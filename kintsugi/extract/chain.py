"""Die Prioritaetskette: laeuft extract.sources in Reihenfolge, erster Treffer gewinnt.

Aufloesung ist **je Feld**: eine Quelle, die ``title`` fuellt aber ``upc`` als
``None`` liefert, darf eine spaetere Quelle ``upc`` fuellen lassen. ``None`` und
``""`` zaehlen beide als leer. Neben den Werten gibt die Kette eine Provenance
zurueck — welche Art welches Feld gewonnen hat —, weil „CSS hat gewonnen, wo
frueher JSON-LD gewann" fuer sich ein Bruchsignal ist (docs/03 §Laeufe).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from kintsugi.extract.base import Extractor, resolve


def _empty(value: object) -> bool:
    return value is None or value == ""


def run_chain(
    sources: Sequence[object],
    doc: object,
    *,
    registry: Mapping[str, Extractor] | None = None,
) -> tuple[dict[str, object], dict[str, str]]:
    """Fuehrt die Quellen in Reihenfolge aus und mischt je Feld first-non-empty.

    ``sources`` sind Objekte mit einem ``kind``-Attribut (SourceSpec). Gibt
    ``(values, provenance)`` zurueck; provenance nennt je gewonnenem Feld die
    Art, die es geliefert hat. Werte sind ``object`` — css liefert ``str | None``,
    strukturierte Quellen auch Listen/Objekte.
    """
    values: dict[str, object] = {}
    provenance: dict[str, str] = {}
    for source in sources:
        kind = source.kind  # type: ignore[attr-defined]
        extractor = registry[kind] if registry is not None else resolve(kind)
        produced = extractor.extract(doc, source)
        for field, value in produced.items():
            if _empty(values.get(field)) and not _empty(value):
                values[field] = value
                provenance[field] = kind
            values.setdefault(field, None)
    return values, provenance
