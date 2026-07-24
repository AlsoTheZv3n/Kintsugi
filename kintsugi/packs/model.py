"""Das Site-Pack als pydantic-Vertrag (ADR-001: Daten, nicht Code).

Jedes Modell ist ``frozen=True, extra="forbid"``: dieses Dokument ist die
einzige Oberflaeche, die ein Heiler umschreiben darf, deshalb ist ein
unbekannter Schluessel ein Ladefehler, nie eine stillschweigend ignorierte
Angabe. YAML-Schluessel sind teils camelCase (``apiVersion``), daher
``populate_by_name=True`` mit Alias.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_BASE = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class _Model(BaseModel):
    model_config = _BASE


class BrowserSpec(_Model):
    """Optionaler Browser-Block des Fetch. Playwright kommt erst in Phase 5."""

    wait_for: str | None = None
    block_resources: list[Literal["image", "font", "media", "stylesheet"]] = Field(
        default_factory=list
    )


class FetchSpec(_Model):
    """Wie geholt wird. Der Fetcher ist Eigenschaft des Packs, nicht global."""

    strategy: Literal["http", "browser"] = "http"
    rate_limit_rps: float = Field(default=0.5, gt=0)
    concurrency: int = Field(default=2, ge=1)
    # respect_robots wird in I0.6.4 zur strukturierten Form erweitert.
    respect_robots: bool = True
    conditional_requests: bool = True
    proxy_pool: Literal["residential", "datacenter"] | None = None
    browser: BrowserSpec | None = None


class DiscoverySpec(_Model):
    """Woher die URLs kommen. Getrennt vom Fetch, weil separat heilbar.

    F1: books.toscrape.com hat keine sitemap.xml (HTTP 404), deshalb ist
    ``pagination`` eine vollwertige, erststufige Strategie mit ``url_template``
    und ``{n}``-Platzhalter — die Phase-0-DoD haengt daran.
    """

    strategy: Literal["sitemap", "pagination", "seed_list", "api"]
    sitemap_url: str | None = None
    url_template: str | None = None
    page_start: int = 1
    page_stop: int | None = None
    seeds: list[str] = Field(default_factory=list)
    url_pattern: str | None = None
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


class SitePack(_Model):
    """Wurzel des Site-Pack-Vertrags. Weitere Bloecke folgen in I0.6.2 bis I0.6.4."""

    api_version: Literal["kintsugi/v1"] = Field(alias="apiVersion")
    domain: str
    entity: str
    version: int = Field(ge=1)
    discovery: DiscoverySpec
    fetch: FetchSpec = Field(default_factory=FetchSpec)
