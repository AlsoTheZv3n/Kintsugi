"""Kommandozeilenoberflaeche von Kintsugi.

Die Definition of Done von Phase 0 lautet woertlich
``uv run kintsugi run books.toscrape.com``. Der Konsolen-Einstiegspunkt existiert
deshalb vor der Pipeline, die er spaeter startet — vorerst als No-op.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from kintsugi import __version__

app = typer.Typer(
    name="kintsugi",
    help="Web-Daten, die sich selbst reparieren, und melden, wenn sie es nicht koennen.",
    no_args_is_help=True,
    add_completion=False,
)

pack_app = typer.Typer(
    name="pack",
    help="Site-Packs pruefen, syncen, Schema ausgeben.",
    no_args_is_help=True,
)
app.add_typer(pack_app)


@pack_app.command("validate")
def pack_validate(
    domain: Annotated[str, typer.Argument()],
    entity: Annotated[str, typer.Argument()],
) -> None:
    """Laedt ein Pack und faehrt die fuenf statischen Pruefungen. Exit 1 bei Fehlern."""
    from kintsugi.packs.loader import PackLoadError, load_pack
    from kintsugi.packs.validate import validate_pack

    try:
        pack = load_pack(domain, entity)
    except PackLoadError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    findings = validate_pack(pack)
    for f in findings:
        typer.echo(f"{f.severity.upper()} {f.check_id} {f.key_path}: {f.message}", err=True)
    if any(f.severity == "error" for f in findings):
        raise typer.Exit(1)
    typer.echo(f"{domain}/{entity}: ok, keine Fehler")


@pack_app.command("sync")
def pack_sync(
    domain: Annotated[str, typer.Argument()],
    entity: Annotated[str, typer.Argument()],
    activate: Annotated[bool, typer.Option("--activate")] = False,
) -> None:
    """Schreibt das Pack als neue Version in die Datenbank; optional aktivieren."""
    from kintsugi.packs.loader import load_pack
    from kintsugi.packs.validate import validate_pack
    from kintsugi.storage.db import transaction
    from kintsugi.storage.packs_repo import activate as activate_pack
    from kintsugi.storage.packs_repo import upsert_pack

    pack = load_pack(domain, entity)
    if any(f.severity == "error" for f in validate_pack(pack)):
        typer.echo("Pack hat Fehler-Findings; nicht synchronisiert.", err=True)
        raise typer.Exit(1)

    with transaction() as conn:
        pack_id = upsert_pack(conn, pack)
        if activate:
            activate_pack(conn, pack_id)
    suffix = " und aktiviert" if activate else ""
    typer.echo(f"{domain}/{entity}: synchronisiert{suffix} ({pack_id})")


@pack_app.command("schema")
def pack_schema() -> None:
    """Gibt das generierte JSON-Schema auf stdout aus."""
    from kintsugi.packs.jsonschema import generate

    typer.echo(json.dumps(generate(), indent=2, sort_keys=True, ensure_ascii=False))


fixtures_app = typer.Typer(
    name="fixtures",
    help="Offline-Fixtures ueber den HttpFetcher aufnehmen.",
    no_args_is_help=True,
)
app.add_typer(fixtures_app)


@fixtures_app.command("capture")
def fixtures_capture(
    domain: Annotated[str, typer.Argument(help="Quelle, muss in der Allowlist stehen")],
    entity: Annotated[str, typer.Option("--entity")],
    label: Annotated[str, typer.Option("--label", help="baseline | edge:<slug> | corpus")],
    url: Annotated[str, typer.Option("--url", help="Abzurufende URL")],
    root: Annotated[Path, typer.Option("--root")] = Path("fixtures"),
) -> None:
    """Nimmt eine Fixture auf — ausschliesslich ueber den HttpFetcher."""
    from kintsugi.harness.fixtures_cli import (
        DomainNotAllowed,
        capture_corpus,
        capture_golden,
        guard_domain,
        write_index,
    )

    # Das Gate steht vor jedem HTTP-Verkehr: eine verbotene Domain kostet null
    # Anfragen (F4).
    try:
        guard_domain(domain)
    except DomainNotAllowed as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    from kintsugi.fetch.http import HttpFetcher

    with HttpFetcher() as fetcher:
        if label == "corpus":
            capture_corpus(root, domain=domain, entity=entity, urls=[url], fetcher=fetcher)
        else:
            capture_golden(
                root, domain=domain, entity=entity, label=label, url=url, fetcher=fetcher
            )
    write_index(root)
    typer.echo(f"{domain}/{entity}: Fixture '{label}' aufgenommen.")


@app.command()
def run(
    domain: Annotated[str, typer.Argument(help="Domain der Quelle, z. B. books.toscrape.com")],
) -> None:
    """Fuehrt einen Lauf fuer die angegebene Domain aus."""
    typer.echo(f"kintsugi {__version__}: Lauf fuer {domain} — noch nicht implementiert.")


@app.command()
def version() -> None:
    """Gibt die Version aus."""
    typer.echo(__version__)


def main() -> None:
    """Einstiegspunkt des Konsolenskripts ``kintsugi``."""
    app()


if __name__ == "__main__":
    main()
