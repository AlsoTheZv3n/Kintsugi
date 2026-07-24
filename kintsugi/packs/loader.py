"""YAML-Loader fuer Site-Packs.

Invariante des gesamten Lebenszyklus: **die YAML-Datei ist die von Menschen
verfasste Quelle der Wahrheit und wird in die ``site_pack``-Tabelle
hochgeschrieben** (docs/03 §Site-Packs). Die Datenbankzeile ist das
Laufzeitartefakt; von Heilern erzeugte Versionen in Phase 2 existieren zuerst
als Zeile und werden nach YAML zurueckexportiert, nie umgekehrt.

Geparst wird ausschliesslich mit ``yaml.safe_load``. ``yaml.load`` und jeder
Loader, der Python-Objektkonstruktion erlaubt, sind verboten: ein Site-Pack ist
ein Dokument mit nicht vertrauenswuerdiger Form, das ein Heiler spaeter schreibt,
und beliebige Objektkonstruktion gaebe ihm genau die Code-Ausfuehrungsflaeche,
die ADR-001 beseitigen soll.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from kintsugi.config import get_settings
from kintsugi.packs.model import SitePack


class PackLoadError(Exception):
    """Ein Site-Pack liess sich nicht laden — mit Datei und Schluesselpfad."""


def _packs_root(root: Path | None) -> Path:
    return root if root is not None else get_settings().packs_dir


def _format_errors(path: Path, exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        key_path = ".".join(str(part) for part in error["loc"])
        lines.append(f"{path}: {key_path} — {error['msg']}")
    return "\n".join(lines)


def _load_file(path: Path) -> SitePack:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        return SitePack.model_validate(raw)
    except ValidationError as exc:
        raise PackLoadError(_format_errors(path, exc)) from exc


def load_pack(domain: str, entity: str, root: Path | None = None) -> SitePack:
    """Laedt ``packs/<domain>/<entity>.yaml`` und prueft Pfad gegen Inhalt."""
    path = _packs_root(root) / domain / f"{entity}.yaml"
    if not path.is_file():
        raise PackLoadError(f"Kein Site-Pack unter {path}")
    pack = _load_file(path)
    if pack.domain != domain or pack.entity != entity:
        raise PackLoadError(
            f"{path}: domain/entity im Dokument ({pack.domain}/{pack.entity}) "
            f"weichen vom Pfad ({domain}/{entity}) ab"
        )
    return pack


def load_packs(root: Path | None = None) -> list[SitePack]:
    """Laedt alle ``packs/*/*.yaml``, deterministisch sortiert, ohne Duplikate."""
    base = _packs_root(root)
    packs: list[SitePack] = []
    seen: dict[tuple[str, str], Path] = {}
    for path in sorted(base.glob("*/*.yaml")):
        pack = _load_file(path)
        expected_domain, expected_entity = path.parent.name, path.stem
        if pack.domain != expected_domain or pack.entity != expected_entity:
            raise PackLoadError(
                f"{path}: domain/entity im Dokument ({pack.domain}/{pack.entity}) "
                f"weichen vom Pfad ({expected_domain}/{expected_entity}) ab"
            )
        key = (pack.domain, pack.entity)
        if key in seen:
            raise PackLoadError(
                f"Doppeltes (domain, entity) {key}: {seen[key]} und {path}"
            )
        seen[key] = path
        packs.append(pack)
    return packs
