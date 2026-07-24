"""Abgeleitete Felder (ADR-013, ``derived_from``).

Das benannte Zuhause, an dem ein Feld ohne eigene Extraktionsquelle entsteht.
Laeuft NACH der Extraktion und den Transforms, VOR der zeilenweisen Validierung.
Beispiel: ``currency`` wird aus dem ``Money`` von ``price`` abgeleitet, und ein
``Money`` wird danach auf seinen ``amount`` ausgepackt, sodass die Validierung
nie einen Nicht-Decimal-Preis sieht.
"""

from __future__ import annotations

from collections.abc import Mapping

from kintsugi.packs.model import FieldSchema
from kintsugi.transform.primitives import Money
from kintsugi.transform.registry import resolve

# Trenner, mit dem eine Mehr-Quellen-Ableitung ihre Segmente verkettet, bevor der
# Transform sie sieht (U+001F, Unit Separator). Ein Trenner ist noetig, damit
# ("ab","c") und ("a","bc") verschiedene Schluessel ergeben; U+001F, weil er in
# echtem Text nicht vorkommt. ``natural_key_16`` kennt ihn und tilgt Rand-Whitespace
# um ihn herum.
_COMPOSITE_SEP = "\x1f"


def apply_derived_fields(
    values: Mapping[str, object], schema_fields: Mapping[str, FieldSchema]
) -> dict[str, object]:
    """Berechnet abgeleitete Felder und packt ``Money`` auf ``amount`` aus.

    Reihenfolge ist wesentlich: erst die Ableitungen aus den (noch nicht
    ausgepackten) Quellwerten berechnen, dann die ``Money``-Werte flach machen.
    """
    result: dict[str, object] = dict(values)

    for name, field in schema_fields.items():
        if field.derived_from is None:
            continue
        df = field.derived_from
        sources = [df.source] if isinstance(df.source, str) else list(df.source)
        transform = resolve(df.transform)
        if transform is None:
            raise ValueError(f"derived_from-Transform {df.transform!r} ist nicht registriert")
        if len(sources) == 1:
            result[name] = transform.fn(result.get(sources[0]))
        else:
            joined = _COMPOSITE_SEP.join(str(result.get(s) or "") for s in sources)
            result[name] = transform.fn(joined)

    for key, value in list(result.items()):
        if isinstance(value, Money):
            result[key] = value.amount

    return result
