"""Ein Pydantic-Modell pro Pack, zeilenweise Validierung mit Quarantaene.

docs/01 §Validation: ein Modell pro Entitaet ist Extraktionsvertrag,
DB-Validierung und API-Schema zugleich. Ablehnung ist strukturiert, nie ein
blosser ValidationError-String. Reason-Codes: ``type_error:<field>``,
``range_violation:<field>``, ``enum_violation:<field>``, ``natural_key_missing``.

Quarantaene (docs/02 §Feldsemantik, docs/06 §Betriebsziele „stille Datenfehler,
die die API erreichen: 0"):

- ``natural_key_missing`` und ``type_error`` sind harte Ablehnungen: die Zeile
  wird gezaehlt und verworfen. Ein kaputter Natural Key korrumpiert den Bestand
  rueckwirkend.
- ``range_violation`` und ``enum_violation`` werden mit den Codes in
  ``record.quality['violations']`` persistiert, aber aus ``gold_book``
  ausgeschlossen — so bleibt die Evidenz fuer den Heiler erhalten, ohne dass ein
  stiller Datenfehler die API erreicht.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from kintsugi.packs.model import FieldSchema, SitePack

_PY_TYPE: dict[str, type] = {
    "string": str,
    "decimal": Decimal,
    "integer": int,
    "boolean": bool,
    "datetime": datetime,
}


@dataclass
class RowResult:
    """Ergebnis der Validierung einer Zeile."""

    accepted: bool
    payload: dict[str, object] | None
    reasons: list[str] = field(default_factory=list)


def _empty(value: object) -> bool:
    return value is None or value == ""


@lru_cache(maxsize=64)
def _lax_model(
    domain: str, entity: str, version: int, fields_key: tuple[tuple[str, str], ...]
) -> type[BaseModel]:
    """Modell nur mit Typen — Bereich/Enum/Pattern pruefen wir selbst.

    Alle Felder sind optional: ``required`` ist eine Fill-Rate-Schwelle, kein
    Ablehnungsgrund. Ein fehlendes Pflichtfeld ist ein Fill-Rate-Miss (der ab
    Phase 2 Heilung ausloest), keine geworfene Ausnahme (docs/02 §Feldsemantik:
    min_fill_rate ist der Wachhund). Nur ein Typfehler und ein fehlender Natural
    Key lehnen die Zeile hart ab. Gecacht je (domain, entity, version).
    """
    definitions: dict[str, Any] = {}
    for name, ftype in fields_key:
        py = _PY_TYPE[ftype]
        definitions[name] = (py | None, None)
    return create_model(
        f"{domain}_{entity}_v{version}",
        __config__=ConfigDict(extra="ignore"),
        **definitions,
    )


def _fields_key(fields: dict[str, FieldSchema]) -> tuple[tuple[str, str], ...]:
    return tuple((name, f.type) for name, f in fields.items())


def build_model(pack: SitePack) -> type[BaseModel]:
    """Das (gecachte) Lax-Modell fuer diesen Pack."""
    return _lax_model(pack.domain, pack.entity, pack.version, _fields_key(pack.schema_.fields))


def validate_row(pack: SitePack, values: dict[str, object]) -> RowResult:
    schema = pack.schema_

    # 1. Natural Key: fehlt eine Komponente, ist die Zeile wertlos (harter Reject).
    for name in schema.natural_key:
        if _empty(values.get(name)):
            return RowResult(accepted=False, payload=None, reasons=["natural_key_missing"])

    # 2. Typen und required (harter Reject bei Verletzung).
    model = build_model(pack)
    try:
        obj = model.model_validate(values)
    except ValidationError as exc:
        reasons = [f"type_error:{error['loc'][0]}" for error in exc.errors()]
        return RowResult(accepted=False, payload=None, reasons=reasons)

    payload = obj.model_dump()

    # 3. Bereich, Enum, Pattern.
    soft: list[str] = []
    for name, fschema in schema.fields.items():
        val = payload.get(name)
        if val is None:
            continue
        if (
            fschema.pattern is not None
            and isinstance(val, str)
            and not re.match(fschema.pattern, val)
        ):
            # Pattern trifft vor allem den Natural Key -> harter Reject.
            return RowResult(accepted=False, payload=None, reasons=[f"type_error:{name}"])
        if fschema.sane_range is not None and isinstance(val, (int, float, Decimal)):
            lo, hi = fschema.sane_range
            if val < lo or val > hi:
                soft.append(f"range_violation:{name}")
        if fschema.enum is not None and val not in fschema.enum:
            soft.append(f"enum_violation:{name}")

    return RowResult(accepted=True, payload=payload, reasons=soft)
