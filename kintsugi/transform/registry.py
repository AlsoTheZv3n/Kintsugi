"""Registry der Transform-Primitiven mit deklarierten Ein- und Ausgabetypen.

docs/02-site-packs.md §Validierung verlangt typvertraegliche Transform-Ketten.
Das ist nur entscheidbar, wenn jede Primitive angibt, was sie konsumiert und
produziert — deshalb steht die Registry vor dem Site-Pack-Validator, der sie
nutzt. `validate_chain` liefert strukturierte Findings, nie eine Ausnahme, damit
ein schlechtes Pack (auch ein kuenftiger Heiler-Vorschlag) vor dem ersten Fetch
abgelehnt wird.

Bewusst KEIN Multi-Output-Transform (ein Transform, der mehrere Zielfelder auf
einmal fuellt): ADR-013 hat diesen Mechanismus verworfen. Felder ohne eigene
Extraktionsquelle entstehen ueber ``derived_from`` (z. B. currency aus dem
Money-Wert von price).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import get_args

from pydantic import BaseModel

# Ein Transform-Typ ist entweder ein konkreter Typ (``str``) oder eine Union
# (``str | None``). Beides wird ueber ``_types_of`` in eine Menge zerlegt.
TypeSpec = object


class Finding(BaseModel):
    """Strukturierter Befund der Ketten- oder Pack-Validierung."""

    code: str
    message: str
    position: int | None = None


@dataclass(frozen=True)
class Transform:
    """Eine benannte Primitive mit deklariertem Ein- und Ausgabetyp."""

    name: str
    fn: Callable[..., object]
    in_type: TypeSpec
    out_type: TypeSpec
    failure_mode: str


REGISTRY: dict[str, Transform] = {}


def register(
    name: str, *, in_type: TypeSpec, out_type: TypeSpec, failure_mode: str
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Registriert die dekorierte Funktion als Transform unter ``name``."""

    def decorator(fn: Callable[..., object]) -> Callable[..., object]:
        if name in REGISTRY:
            raise ValueError(f"Transform {name!r} ist bereits registriert")
        REGISTRY[name] = Transform(
            name=name, fn=fn, in_type=in_type, out_type=out_type, failure_mode=failure_mode
        )
        return fn

    return decorator


def resolve(name: str) -> Transform | None:
    """Der Transform zu ``name`` oder ``None``, wenn unbekannt."""
    return REGISTRY.get(name)


def apply_transforms(names: Sequence[str], value: object) -> object:
    """Wendet die Transform-Kette der Reihe nach auf ``value`` an.

    ``validate_chain`` (statisch, zur Pack-Ladezeit) garantiert, dass die Namen
    bekannt und typvertraeglich sind; zur Laufzeit reduziert diese Funktion den
    Rohwert durch die Kette. Ein unbekannter Name hier waere ein Programmierfehler
    (das Pack haette nicht laden duerfen) und fliegt als ``KeyError``.
    """
    for name in names:
        transform = REGISTRY[name]
        value = transform.fn(value)
    return value


def _types_of(spec: TypeSpec) -> frozenset[object]:
    args = get_args(spec)
    return frozenset(args) if args else frozenset({spec})


def _compatible(produced: TypeSpec, expected: TypeSpec) -> bool:
    """Vertraeglich, wenn sich die moeglichen Typen ueberschneiden.

    Ueberschneidung statt Teilmenge: ``strip`` produziert ``str | None``,
    ``parse_currency`` erwartet ``str`` — der gemeinsame ``str`` genuegt, damit
    die Kette ``[strip, parse_currency]`` gueltig ist.
    """
    return bool(_types_of(produced) & _types_of(expected))


def validate_chain(names: Sequence[str]) -> list[Finding]:
    """Prueft eine Transform-Kette ab ``str`` auf Typvertraeglichkeit.

    Gibt eine Liste von Findings zurueck (leer = gueltig). Codes:
    ``unknown_transform`` und ``transform_type_mismatch``.
    """
    findings: list[Finding] = []
    produced: TypeSpec = str
    for index, name in enumerate(names):
        transform = resolve(name)
        if transform is None:
            findings.append(
                Finding(
                    code="unknown_transform",
                    message=f"Transform {name!r} ist nicht registriert",
                    position=index,
                )
            )
            return findings  # ohne bekannten Ausgabetyp ist der Rest nicht pruefbar
        if not _compatible(produced, transform.in_type):
            findings.append(
                Finding(
                    code="transform_type_mismatch",
                    message=(
                        f"Transform {name!r} erwartet {transform.in_type}, bekommt aber {produced}"
                    ),
                    position=index,
                )
            )
        produced = transform.out_type
    return findings
