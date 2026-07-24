"""expected.json: das Post-Transform-Soll je Fixture (I1.3.4).

docs/04-self-healing.md §Freigabe-Gate („alle Pflichtfelder exakt korrekt,
optionale Felder mindestens auf altem Niveau") und docs/02 §Beispiel. Jede
Fixture traegt das Ergebnis von Extraktor **plus** Transform-Kette — nicht den
Roh-HTML-Text. Die Feldliste stammt aus ``schema.fields``, nicht aus
``extract.sources`` (F3): ``currency`` ist deklariert-pflichtig, hat aber keine
Quelle und wird von ``parse_currency`` aus dem Symbol abgeleitet — ``expected.json``
traegt deshalb ``currency: "GBP"`` auf jeder books-Fixture.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

__all__ = ["ExpectedField", "ExpectedFixture"]


class ExpectedField(BaseModel):
    """Ein Sollwert plus die required-Markierung aus ``schema.fields``."""

    model_config = ConfigDict(extra="forbid")

    value: str | int | float | bool | None
    required: bool


class ExpectedFixture(BaseModel):
    """Das erwartete Ergebnis eines Fixture-Replays.

    Einzeilig (books: eine Entitaet je Detailseite) traegt ``fields`` die eine
    Zeile; mehrzeilig (quotes: N Entitaeten je /js/-Seite) traegt ``rows`` die
    Sollwerte je Zeile in Reihenfolge, und ``fields`` bleibt leer. ``rows`` ist
    additiv: bestehende Einzeiler-``expected.json`` ohne ``rows`` bleiben gueltig.
    """

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, ExpectedField] = {}
    rows: list[dict[str, ExpectedField]] | None = None
    expected_row_count: int
    expected_natural_keys: list[str]

    def expected_rows(self) -> list[dict[str, ExpectedField]]:
        """Die Soll-Zeilen: ``rows`` mehrzeilig, sonst die eine ``fields``-Zeile."""
        return self.rows if self.rows is not None else [self.fields]
