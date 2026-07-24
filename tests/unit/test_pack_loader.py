"""Prueft den YAML-Pack-Loader (I0.6.6)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from kintsugi.packs.loader import PackLoadError, load_pack, load_packs

VALID_PACK = {
    "apiVersion": "kintsugi/v1",
    "domain": "example.com",
    "entity": "book",
    "version": 1,
    "discovery": {"strategy": "pagination", "url_template": "https://example.com/p-{n}.html"},
    "extract": {"sources": [{"kind": "css", "fields": {"title": {"selector": "h1"}}}]},
    "schema": {"natural_key": ["upc"], "fields": {"upc": {"type": "string", "required": True}}},
    "compliance": {
        "tos_url": "https://example.com/",
        "tos_verdict": "permits",
        "tos_reviewed_at": "2026-07-21",
        "reviewed_by": "human:sven",
        "robots_checked_at": "2026-07-21",
        "public_content": True,
        "personal_data": False,
    },
}


def _write(root: Path, domain: str, entity: str, body: dict) -> Path:
    d = root / domain
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{entity}.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def test_gueltiges_pack_laedt(tmp_path):
    _write(tmp_path, "example.com", "book", VALID_PACK)
    pack = load_pack("example.com", "book", root=tmp_path)
    assert pack.domain == "example.com"


def test_fehlerhaftes_pack_nennt_datei_und_schluesselpfad(tmp_path):
    body = {**VALID_PACK}
    body["schema"] = {
        "natural_key": ["upc"],
        "fields": {"price": {"type": "decimal", "required": True, "min_fill_rate": 1.5}},
    }
    _write(tmp_path, "example.com", "book", body)
    with pytest.raises(PackLoadError) as exc:
        load_pack("example.com", "book", root=tmp_path)
    msg = str(exc.value)
    assert "example.com" in msg
    assert "schema.fields.price.min_fill_rate" in msg


def test_domain_widerspruch_wird_abgelehnt(tmp_path):
    body = {**VALID_PACK, "domain": "other.com"}
    _write(tmp_path, "example.com", "book", body)
    with pytest.raises(PackLoadError, match="weichen vom Pfad"):
        load_pack("example.com", "book", root=tmp_path)


def test_zweite_datei_fuer_dasselbe_entity_wird_abgelehnt(tmp_path):
    """Eine zweite Datei, die dasselbe (domain, entity) beansprucht, muss werfen.

    Der Pfad-Inhalt-Check faengt es: die zweite Datei liegt unter einem anderen
    Stem, deklariert aber entity=book, also weicht der Inhalt vom Pfad ab.
    """
    _write(tmp_path, "example.com", "book", VALID_PACK)
    (tmp_path / "example.com" / "duplicate.yaml").write_text(
        yaml.safe_dump(VALID_PACK), encoding="utf-8"
    )
    with pytest.raises(PackLoadError, match="weichen vom Pfad"):
        load_packs(root=tmp_path)


def test_loader_nutzt_nur_safe_load():
    source = Path("kintsugi/packs/loader.py").read_text(encoding="utf-8")
    assert "yaml.load(" not in source
    assert "Loader=" not in source
    assert "yaml.safe_load" in source


def test_fehlende_datei_wirft(tmp_path):
    with pytest.raises(PackLoadError, match="Kein Site-Pack"):
        load_pack("nicht.da", "book", root=tmp_path)
