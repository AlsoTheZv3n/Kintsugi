"""Das Site-Pack als pydantic-Vertrag (ADR-001: Daten, nicht Code).

Jedes Modell ist ``frozen=True, extra="forbid"``: dieses Dokument ist die
einzige Oberflaeche, die ein Heiler umschreiben darf, deshalb ist ein
unbekannter Schluessel ein Ladefehler, nie eine stillschweigend ignorierte
Angabe. YAML-Schluessel sind teils camelCase (``apiVersion``), daher
``populate_by_name=True`` mit Alias.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kintsugi.fetch.block_detect import SignatureOverride
from kintsugi.packs.denylist import check_domain, check_no_credentials

_BASE = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class _Model(BaseModel):
    model_config = _BASE


# Welche Phase den Executor einer Source-Art liefert. Nur css laeuft in Phase 0.
# Die Reihenfolge in extract.sources ist die Prioritaet aus docs/01 (erster
# Treffer gewinnt); dieser Zuordnung entnimmt der Extraktor die Fehlermeldung
# fuer noch nicht gebaute Arten.
SOURCE_PHASE: dict[str, str] = {
    "api": "Phase 5",
    "jsonld": "Phase 1",
    "embedded_json": "Phase 1",
    "xhr": "Phase 1",
    "css": "Phase 0",
    "llm": "Phase 4",
}


def stub_execute(kind: str) -> None:
    """Platzhalter fuer noch nicht gebaute Extraktoren; nennt die liefernde Phase."""
    phase = SOURCE_PHASE.get(kind, "einer spaeteren Phase")
    raise NotImplementedError(f"Extraktor fuer kind={kind!r} kommt in {phase}")


class BrowserSpec(_Model):
    """Optionaler Browser-Block des Fetch. Playwright kommt erst in Phase 5."""

    wait_for: str | None = None
    block_resources: list[Literal["image", "font", "media", "stylesheet"]] = Field(
        default_factory=list
    )


class RobotsOverride(_Model):
    """Dokumentierte Ausnahme von der robots.txt (README: nicht ohne Eintrag).

    ``respect_robots: false`` ist damit nicht darstellbar — die README-Zusage
    „nicht abschaltbar ohne dokumentierten Site-Pack-Eintrag" wird eine
    Typbedingung statt einer Gewohnheit.
    """

    override: Literal[True]
    reason: str
    approved_by: str
    approved_at: date
    evidence_url: str


class FetchSpec(_Model):
    """Wie geholt wird. Der Fetcher ist Eigenschaft des Packs, nicht global."""

    strategy: Literal["http", "browser"] = "http"
    rate_limit_rps: float = Field(default=0.5, gt=0)
    concurrency: int = Field(default=2, ge=1)
    # Nur True oder eine dokumentierte Ausnahme; false ist nicht darstellbar.
    respect_robots: Literal[True] | RobotsOverride = True
    conditional_requests: bool = True
    proxy_pool: Literal["residential", "datacenter"] | None = None
    browser: BrowserSpec | None = None
    # Pack-Overrides der globalen Signaturlisten (I1.4.2): default anhaengen,
    # ``replace: true`` tauscht die globale Liste aus. Dasselbe Entry-Modell wie
    # die Fetch-Schicht, ein schlechtes Pack scheitert also statisch, nicht im Fetch.
    block_signatures: SignatureOverride | None = None
    soft_404_signatures: SignatureOverride | None = None


# Ein benannter Alias, damit die Discovery-Registry (kintsugi/discovery) und das
# Pack-Schema aus derselben Quelle lesen und nie auseinanderdriften koennen.
DiscoveryStrategyName = Literal["sitemap", "pagination", "seed_list", "api"]


class DiscoverySpec(_Model):
    """Woher die URLs kommen. Getrennt vom Fetch, weil separat heilbar.

    F1: books.toscrape.com hat keine sitemap.xml (HTTP 404), deshalb ist
    ``pagination`` eine vollwertige, erststufige Strategie mit ``url_template``
    und ``{n}``-Platzhalter — die Phase-0-DoD haengt daran.
    """

    strategy: DiscoveryStrategyName
    sitemap_url: str | None = None
    url_template: str | None = None
    page_start: int = 1
    page_stop: int | None = None
    seeds: list[str] = Field(default_factory=list)
    url_pattern: str | None = None
    # CSS-Selektor der Produktlinks auf einer Index-Seite (nur pagination).
    link_selector: str | None = None
    max_urls_per_run: int = Field(default=1000, ge=1)

    @field_validator("url_pattern")
    @classmethod
    def _compilable(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                msg = f"url_pattern ist kein gueltiger regulaerer Ausdruck: {exc}"
                raise ValueError(msg) from exc
        return value

    @model_validator(mode="after")
    def _strategy_requirements(self) -> DiscoverySpec:
        if self.strategy == "sitemap" and not self.sitemap_url:
            raise ValueError("discovery.strategy 'sitemap' braucht sitemap_url")
        if self.strategy == "pagination":
            if not self.url_template:
                raise ValueError("discovery.strategy 'pagination' braucht url_template")
            if "{n}" not in self.url_template:
                raise ValueError("url_template muss den Platzhalter {n} enthalten")
        if self.strategy == "seed_list" and not self.seeds:
            raise ValueError("discovery.strategy 'seed_list' braucht eine nicht-leere seeds-Liste")
        return self


# --------------------------------------------------------------------------
# Extraction: priorisierte Source-Liste, diskriminierte Union auf `kind`
# (docs/01 Extraktionsleiter). Kein `const`-Kind — ADR-013 waehlt derived_from
# auf dem Schema-Feld (siehe FieldSchema), nicht eine Source-Art.
# --------------------------------------------------------------------------


class FieldExtract(_Model):
    """Wie ein einzelnes Feld aus dem DOM geholt wird (css-Source)."""

    selector: str
    attr: str | None = None  # Attribut statt Textinhalt, z. B. data-price
    anchor_hint: str | None = None  # Freitext nur fuer die Heilung (docs/02)
    transform: list[str] = Field(default_factory=list)


class ApiSource(_Model):
    kind: Literal["api"]
    endpoint: str | None = None


class JsonLdSource(_Model):
    kind: Literal["jsonld"]
    type: str  # schema.org @type, case-sensitiv; erster Treffer in Dokumentreihenfolge
    # Optionale Feld-Map (Feldname -> jsonpath relativ zum getroffenen Objekt).
    # Fehlt sie, werden die Top-Level-Keys des Objekts direkt als Felder genommen.
    fields: dict[str, str] | None = None
    # Optionale per-Feld-Transform-Kette (wie css), z. B. strip auf einen Rohwert.
    transforms: dict[str, list[str]] = Field(default_factory=dict)


class EmbeddedJsonSource(_Model):
    kind: Literal["embedded_json"]
    script_id: str | None = None  # z. B. __NEXT_DATA__ (script-id-Modus)
    # F5: quotes.toscrape.com/js legt die Daten als `var data = [...]` auf einem
    # script-Tag OHNE id-Attribut ab. var_name findet die Variablenzuweisung per
    # Name (inline-js-var-Modus) — genau der Fall, den script_id nicht adressiert.
    var_name: str | None = None
    root: str | None = None  # jsonpath/dotted-Pfad in die (Zeilen-)Nutzlast
    # Optionale Feld-Map (Feldname -> jsonpath relativ zur Zeile), gleiche Semantik
    # wie bei jsonld. Fehlt sie, werden die Top-Level-Keys der Zeile direkt genommen.
    # #104: quotes' author ist ein verschachteltes Objekt, also author -> $.author.name.
    fields: dict[str, str] | None = None
    # Optionale per-Feld-Transform-Kette (wie css), z. B. strip auf einen Rohwert.
    transforms: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _exactly_one_locator(self) -> EmbeddedJsonSource:
        # Genau eins von script_id / var_name — nie beide, nie keins.
        if bool(self.script_id) == bool(self.var_name):
            raise ValueError("embedded_json braucht genau eins von script_id oder var_name")
        return self


class XhrSource(_Model):
    kind: Literal["xhr"]
    # Endpunkt-URL oder Template mit {name}-Platzhaltern, die aus den
    # Query-Parametern der entdeckten Seiten-URL gefuellt werden.
    endpoint: str | None = None
    method: Literal["GET"] = "GET"  # der Fetcher kann nur GET; POST waere Phase 5+
    # Zusaetzliche Query-Parameter (Werte duerfen {name}-Platzhalter tragen).
    params: dict[str, str] = Field(default_factory=dict)
    # Zusaetzliche Request-Header (z. B. X-Requested-With). Die Zugangsdaten-Sperre
    # (denylist) durchsucht sie mit: ein Authorization/Cookie-Header laesst das Pack
    # gar nicht erst laden.
    headers: dict[str, str] = Field(default_factory=dict)
    # jsonpath auf die Zeilen (bare Array: '$[*]') und die Feld-Map je Zeile.
    row_root: str | None = None
    fields: dict[str, str] | None = None
    # Optionale per-Feld-Transform-Kette (wie css), z. B. strip auf title.
    transforms: dict[str, list[str]] = Field(default_factory=dict)


class CssSource(_Model):
    kind: Literal["css"]
    row_selector: str | None = None  # None = eine Entitaet pro Seite
    fields: dict[str, FieldExtract]


class LlmSource(_Model):
    kind: Literal["llm"]


SourceSpec = Annotated[
    ApiSource | JsonLdSource | EmbeddedJsonSource | XhrSource | CssSource | LlmSource,
    Field(discriminator="kind"),
]


class ExtractSpec(_Model):
    """Priorisierte Liste von Quellen; die Reihenfolge ist die Prioritaet."""

    sources: list[SourceSpec]


# --------------------------------------------------------------------------
# Schema, Qualitaet, Heilung, Auslieferung
# --------------------------------------------------------------------------

FieldType = Literal["string", "decimal", "integer", "boolean", "datetime"]


class DerivedFrom(_Model):
    """ADR-013: ein Feld ohne eigene Extraktionsquelle wird berechnet.

    ``source`` nennt ein Feld oder eine Liste von Feldern, ``transform`` den
    registrierten Transform (z. B. currency_from_symbol, sha256_slug).
    """

    source: str | list[str]
    transform: str


class FieldSchema(_Model):
    """Vertrag eines einzelnen Zielfelds (docs/02 §Feldsemantik)."""

    type: FieldType
    required: bool = False
    min_fill_rate: float = Field(ge=0.0, le=1.0)
    sane_range: tuple[float, float] | None = None
    enum: list[str] | None = None
    pattern: str | None = None
    derived_from: DerivedFrom | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_min_fill_rate(cls, data: object) -> object:
        # docs/02 nennt min_fill_rate den eigentlichen Wachhund: ein Pflichtfeld
        # darf nie auf einen ungeprueften Schwellwert defaulten. Fehlt der Wert,
        # ist er 1.0 fuer required, sonst 0.0.
        if isinstance(data, dict) and "min_fill_rate" not in data:
            data = dict(data)
            data["min_fill_rate"] = 1.0 if data.get("required") else 0.0
        return data

    @field_validator("pattern")
    @classmethod
    def _compilable(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                msg = f"pattern ist kein gueltiger regulaerer Ausdruck: {exc}"
                raise ValueError(msg) from exc
        return value


class SchemaSpec(_Model):
    natural_key: list[str] = Field(min_length=1)
    fields: dict[str, FieldSchema]


class QualitySpec(_Model):
    min_rows_per_run: int = Field(default=1, ge=0)
    row_count_deviation: float = Field(default=0.30, ge=0)
    max_duplicate_rate: float = Field(default=0.02, ge=0, le=1)
    # docs/04 §Ausloeser nennt "Bereichsverletzungen ueber 5 %" als Trigger, hat
    # aber im docs/02-Beispiel keinen deklarativen Ort. Dieses Feld ist er, damit
    # das Qualitaetsprofil in Phase 1 einen Schwellwert liest statt einer
    # hartcodierten Konstante.
    max_range_violation_rate: float = Field(default=0.05, ge=0, le=1)
    # Phase 0 hat keinen Baseline-Lauf, also sind die Schwellwerte provisorisch;
    # die ydata-profiling-Baseline in Phase 1 ersetzt sie, statt einen Schaetzwert
    # still zu erben.
    thresholds_source: Literal["provisional", "baseline"] = "provisional"


class HealingSpec(_Model):
    enabled: bool = False
    max_auto_versions_per_window: int = Field(default=3, ge=0)
    window: str = "7d"
    require_golden_pass: bool = True
    canary_fraction: float = Field(default=0.05, ge=0, le=1)
    canary_min_rows: int = Field(default=50, ge=0)
    escalate_on: list[str] = Field(default_factory=list)


Sink = Literal["postgres", "webhook", "parquet"]


def _default_sinks() -> list[Sink]:
    return ["postgres"]


class DeliverySpec(_Model):
    sinks: list[Sink] = Field(default_factory=_default_sinks)
    webhook_on_change: str | None = None


class ComplianceSpec(_Model):
    """Pflichtantworten auf die Compliance-Zusagen der README.

    Ein Pack, das diese Fragen nicht beantwortet, laesst sich gar nicht laden.
    """

    tos_url: str
    tos_verdict: Literal["permits", "silent", "forbids"]
    tos_reviewed_at: date
    reviewed_by: str
    robots_checked_at: date
    public_content: bool
    personal_data: bool
    personal_data_fields: list[str] = Field(default_factory=list)
    legal_basis: str | None = None

    @model_validator(mode="after")
    def _admissible(self) -> ComplianceSpec:
        if self.tos_verdict == "forbids":
            raise ValueError("Quelle verbietet automatisierten Zugriff (tos_verdict=forbids)")
        if self.personal_data and not (self.legal_basis and self.legal_basis.strip()):
            raise ValueError("personal_data=true verlangt eine nicht-leere legal_basis")
        return self


class SitePack(_Model):
    """Wurzel des Site-Pack-Vertrags."""

    api_version: Literal["kintsugi/v1"] = Field(alias="apiVersion")
    domain: str
    entity: str
    version: int = Field(ge=1)
    notes: str | None = None  # Freitext, z. B. Belege fuer Annahmen (docs/03 §Site-Packs)
    discovery: DiscoverySpec
    fetch: FetchSpec = Field(default_factory=FetchSpec)
    extract: ExtractSpec
    schema_: SchemaSpec = Field(alias="schema")
    quality: QualitySpec = Field(default_factory=QualitySpec)
    healing: HealingSpec = Field(default_factory=HealingSpec)
    delivery: DeliverySpec = Field(default_factory=DeliverySpec)
    compliance: ComplianceSpec

    @model_validator(mode="before")
    @classmethod
    def _reject_credentials(cls, data: object) -> object:
        # Vor der Feldvalidierung: das ganze rohe Dokument nach
        # Zugangsdaten-Schluesseln durchsuchen, auch in dict-typisierten Bloecken.
        check_no_credentials(data)
        return data

    @model_validator(mode="after")
    def _reject_denied_domain(self) -> SitePack:
        # In der Modellvalidierung, nicht nur in der CLI: ein Heiler-Vorschlag
        # trifft die Sperre kostenlos.
        check_domain(self.domain)
        return self
