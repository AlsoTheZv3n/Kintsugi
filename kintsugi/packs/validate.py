"""Die fuenf statischen Site-Pack-Pruefungen aus docs/02 §Validierung.

``validate_pack`` gibt Findings zurueck, statt zu werfen: docs/04 §Freigabe-Gate
faehrt dieselbe Funktion ueber Heiler-Vorschlaege und braucht den Ablehnungsgrund
als Incident-Evidence. „Der billigste abgelehnte Vorschlag ist der, fuer den nie
ein Fixture-Replay gestartet wurde."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from selectolax.lexbor import LexborHTMLParser

from kintsugi.packs.model import CssSource, SitePack
from kintsugi.transform.registry import validate_chain

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Finding:
    check_id: str
    severity: Severity
    key_path: str
    message: str


def validate_pack(pack: SitePack) -> list[Finding]:
    findings: list[Finding] = []
    findings += _check_selectors(pack)
    findings += _check_fields_sourced(pack)
    findings += _check_natural_key(pack)
    findings += _check_transform_chains(pack)
    findings += _check_required_min_fill_rate(pack)
    return findings


# 1 -------------------------------------------------------------------------


def _check_selectors(pack: SitePack) -> list[Finding]:
    """Jeder css-Selektor parst — mit selectolax, der Engine, die ihn ausfuehrt."""
    findings: list[Finding] = []
    tree = LexborHTMLParser("<html><body></body></html>")
    for i, source in enumerate(pack.extract.sources):
        if not isinstance(source, CssSource):
            continue
        base = f"extract.sources[{i}]"
        if source.row_selector is not None:
            findings += _try_selector(tree, source.row_selector, f"{base}.row_selector")
        for name, field in source.fields.items():
            findings += _try_selector(tree, field.selector, f"{base}.fields.{name}.selector")
    return findings


def _try_selector(tree: LexborHTMLParser, selector: str, key_path: str) -> list[Finding]:
    try:
        tree.css(selector)
    except Exception as exc:  # selectolax: SelectolaxError bei ungueltigem Selektor
        return [Finding("selector_parse", "error", key_path, f"Selektor parst nicht: {exc}")]
    return []


# 2 -------------------------------------------------------------------------


def _check_fields_sourced(pack: SitePack) -> list[Finding]:
    """Jedes deklarierte Feld hat eine Quelle: css-Feld, derived_from oder eine
    strukturelle Source (jsonld/embedded_json/api/xhr liefern alle Felder)."""
    css_field_names: set[str] = set()
    has_structural_source = False
    for source in pack.extract.sources:
        if isinstance(source, CssSource):
            css_field_names |= set(source.fields)
        else:
            has_structural_source = True

    findings: list[Finding] = []
    for name, field in pack.schema_.fields.items():
        sourced = name in css_field_names or field.derived_from is not None or has_structural_source
        if not sourced:
            findings.append(
                Finding(
                    "field_without_source",
                    "error",
                    f"schema.fields.{name}",
                    f"Feld {name!r} hat weder eine Extraktionsquelle noch derived_from",
                )
            )
    return findings


# 3 -------------------------------------------------------------------------


def _check_natural_key(pack: SitePack) -> list[Finding]:
    findings: list[Finding] = []
    for i, name in enumerate(pack.schema_.natural_key):
        field = pack.schema_.fields.get(name)
        if field is None:
            findings.append(
                Finding(
                    "natural_key_missing",
                    "error",
                    f"schema.natural_key[{i}]",
                    f"natural_key {name!r} ist kein deklariertes Feld",
                )
            )
        elif not field.required:
            findings.append(
                Finding(
                    "natural_key_optional",
                    "error",
                    f"schema.fields.{name}.required",
                    f"natural_key {name!r} muss required sein",
                )
            )
    return findings


# 4 -------------------------------------------------------------------------


def _check_transform_chains(pack: SitePack) -> list[Finding]:
    findings: list[Finding] = []
    for i, source in enumerate(pack.extract.sources):
        if not isinstance(source, CssSource):
            continue
        for name, field in source.fields.items():
            if not field.transform:
                continue
            key_path = f"extract.sources[{i}].fields.{name}.transform"
            for chain_finding in validate_chain(field.transform):
                findings.append(
                    Finding("transform_chain", "error", key_path, chain_finding.message)
                )
    return findings


# 5 -------------------------------------------------------------------------


def _check_required_min_fill_rate(pack: SitePack) -> list[Finding]:
    findings: list[Finding] = []
    for name, field in pack.schema_.fields.items():
        if field.required and field.min_fill_rate < 0.5:
            findings.append(
                Finding(
                    "min_fill_rate_too_low",
                    "error",
                    f"schema.fields.{name}.min_fill_rate",
                    f"required-Feld {name!r} hat min_fill_rate {field.min_fill_rate} < 0.5",
                )
            )
    return findings
