"""Gemeinsame jsonpath-Feld-Map fuer die strukturierten Extraktoren.

jsonld (#102), embedded_json (#104) und der kommende xhr (#103) bilden Felder
identisch ab: **Feldname -> jsonpath** relativ zum getroffenen Objekt bzw. zur
Zeile. Ein Ausdruck ohne fuehrendes ``$`` wird als ``$.<pfad>`` gelesen. Trifft
der Pfad genau einen Wert, ist das Feld dieser Wert; trifft er mehrere, die Liste;
trifft er keinen, bleibt das Feld **weg** (ein Fehltreffer ist ein Fill-Rate-
Signal, kein Fehler — dieselbe Regel wie ein leerer css-Selektor).

Die Ausdruecke werden **einmal** kompiliert und ueber alle Zeilen einer
Mehrzeilen-Seite wiederverwendet (bei quotes 10 Zeilen, bei xhr N ueber ``$[*]``)
— sonst kostete jede Zeile einen erneuten jsonpath-Parse.
"""

from __future__ import annotations

from typing import Any

from jsonpath_ng import parse as jsonpath_parse

__all__ = ["CompiledFieldMap", "apply_field_map", "compile_field_map", "map_fields"]

# jsonpath_ng ist mypy-seitig untypisiert (ignore_missing_imports), der
# kompilierte Ausdruck ist daher Any; wir rufen nur ``.find``.
CompiledFieldMap = list[tuple[str, Any]]


def compile_field_map(fields: dict[str, str]) -> CompiledFieldMap:
    """Kompiliert die Feld-Map einmal (fuehrendes ``$`` optional)."""
    return [
        (name, jsonpath_parse(path if path.startswith("$") else "$." + path))
        for name, path in fields.items()
    ]


def apply_field_map(compiled: CompiledFieldMap, obj: object) -> dict[str, object]:
    """Wendet eine kompilierte Feld-Map auf ein Objekt/eine Zeile an."""
    out: dict[str, object] = {}
    for name, expr in compiled:
        found: list[object] = [match.value for match in expr.find(obj)]
        if found:
            out[name] = found[0] if len(found) == 1 else found
    return out


def map_fields(obj: object, fields: dict[str, str]) -> dict[str, object]:
    """Bequemlichkeit fuer den Einzelaufruf (jsonld: ein Objekt je Seite)."""
    return apply_field_map(compile_field_map(fields), obj)
