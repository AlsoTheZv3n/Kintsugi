"""Generiert schema/site-pack.schema.json aus dem SitePack-Modell.

docs/02 §Validierung verlangt ein JSON-Schema, das aus dem Pydantic-Modell
generiert wird; docs/04 §LLM-Vorschlag verlangt, dass das Modell einen
Site-Pack-Patch „ausschliesslich als JSON gegen ein festes Schema" liefert.
Dieses Schema muss eine committete Datei sein, weil es in Phase 4 einem
LLM-Anbieter als Structured-Output-Constraint uebergeben wird und nicht zur
Prompt-Zeit aus einem Python-Import entstehen kann.

Die Ausgabe ist byte-stabil: keine Zeitstempel, keine Generator-Version, feste
Serialisierung — sonst schluege der Drift-Waechter bei jedem Commit an.
"""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

from kintsugi.packs.model import SitePack

SCHEMA_PATH = Path("schema/site-pack.schema.json")

_ID = "https://github.com/AlsoTheZv3n/Kintsugi/schema/site-pack.schema.json"


def generate() -> dict[str, object]:
    """Das JSON-Schema als dict, mit $id/title und ohne zeitabhaengige Teile."""
    schema = SitePack.model_json_schema(by_alias=True, mode="validation")
    schema["$id"] = _ID
    schema["title"] = "Kintsugi Site Pack"
    schema["description"] = (
        "Generiertes Build-Artefakt aus kintsugi/packs/model.py — nicht von Hand "
        "bearbeiten. Erzeugen mit `uv run python -m kintsugi.packs.jsonschema`."
    )
    return schema


def _serialise(schema: dict[str, object]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(path: Path = SCHEMA_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialise(generate()), encoding="utf-8")


def _check(path: Path = SCHEMA_PATH) -> int:
    wanted = _serialise(generate())
    if not path.is_file():
        sys.stderr.write(f"{path} fehlt — mit `python -m kintsugi.packs.jsonschema` erzeugen\n")
        return 1
    current = path.read_text(encoding="utf-8")
    if current == wanted:
        return 0
    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        wanted.splitlines(keepends=True),
        fromfile=f"{path} (committet)",
        tofile=f"{path} (generiert)",
    )
    sys.stderr.write("".join(diff))
    sys.stderr.write(
        f"\n{path} weicht vom Modell ab — mit `python -m kintsugi.packs.jsonschema` neu erzeugen\n"
    )
    return 1


def main() -> int:
    if "--check" in sys.argv[1:]:
        return _check()
    write()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
