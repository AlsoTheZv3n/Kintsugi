"""Kommandozeilenoberflaeche von Kintsugi.

Die Definition of Done von Phase 0 lautet woertlich
``uv run kintsugi run books.toscrape.com``. Der Konsolen-Einstiegspunkt existiert
deshalb vor der Pipeline, die er spaeter startet — vorerst als No-op.
"""

from __future__ import annotations

import typer

from kintsugi import __version__

app = typer.Typer(
    name="kintsugi",
    help="Web-Daten, die sich selbst reparieren, und melden, wenn sie es nicht koennen.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def run(
    domain: str = typer.Argument(..., help="Domain der Quelle, z. B. books.toscrape.com"),
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
