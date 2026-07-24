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
    entity: Annotated[str | None, typer.Option("--entity")] = None,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    max_urls: Annotated[int | None, typer.Option("--max-urls")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Fuehrt einen Lauf fuer die angegebene Domain aus.

    Exit-Codes: 0 fuer ok und degraded (mit Warnzeile), 1 fuer failed, 2 fuer
    Bedien-/Konfigurationsfehler wie ein fehlendes aktives Pack.
    """
    from kintsugi.runner import NoActivePackError
    from kintsugi.runner import run as run_pipeline

    try:
        result = run_pipeline(
            domain, entity=entity, limit=limit, max_urls=max_urls, dry_run=dry_run
        )
    except NoActivePackError as exc:
        typer.echo(f"no active site pack: {exc}", err=True)
        raise typer.Exit(2) from exc

    c = result.counters
    typer.echo(
        f"run {result.run_id} status={result.status} "
        f"rows_considered={c.rows_considered} rows_extracted={c.rows_extracted} "
        f"rows_valid={c.rows_valid} rows_inserted={c.rows_inserted} "
        f"rows_versioned={c.rows_versioned} rows_unchanged={c.rows_unchanged}"
    )
    if result.status == "failed":
        typer.echo(f"FAILED: {result.error}", err=True)
        raise typer.Exit(1)
    if result.status == "degraded":
        typer.echo(f"WARN: Lauf degraded ({result.error or 'Teilausfall'})", err=True)
    raise typer.Exit(0)


@app.command()
def sources() -> None:
    """Listet je (domain, entity): aktive Version, letzter Lauf, aktuelle Records."""
    from sqlalchemy import func, select, true

    from kintsugi.storage.db import transaction
    from kintsugi.storage.tables import record, site_pack
    from kintsugi.storage.tables import run as run_t

    # Der juengste Lauf je aktivem Pack ueber einen Lateral-Join.
    latest = (
        select(
            run_t.c.status.label("status"),
            run_t.c.started_at.label("started_at"),
        )
        .where(run_t.c.site_pack_id == site_pack.c.id)
        .order_by(run_t.c.started_at.desc())
        .limit(1)
        .lateral("lr")
    )
    with transaction() as conn:
        rows = conn.execute(
            select(
                site_pack.c.domain,
                site_pack.c.entity,
                site_pack.c.version,
                latest.c.started_at,
                latest.c.status,
            )
            .select_from(site_pack.outerjoin(latest, true()))
            .where(site_pack.c.status == "active")
            .order_by(site_pack.c.domain, site_pack.c.entity)
        ).all()
        counts: dict[str, int] = {
            entity: count
            for entity, count in conn.execute(
                select(record.c.entity, func.count())
                .where(record.c.valid_to.is_(None))
                .group_by(record.c.entity)
            ).all()
        }
    if not rows:
        typer.echo("keine aktiven Packs.")
        return
    for r in rows:
        typer.echo(
            f"{r.domain}/{r.entity} v{r.version} "
            f"last_run={r.started_at} status={r.status} records={counts.get(r.entity, 0)}"
        )


@app.command()
def version() -> None:
    """Gibt die Version aus."""
    typer.echo(__version__)


def main() -> None:
    """Einstiegspunkt des Konsolenskripts ``kintsugi``."""
    app()


if __name__ == "__main__":
    main()
