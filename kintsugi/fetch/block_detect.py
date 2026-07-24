"""Erkennt Blockaden (Consent-Wall/Challenge/CAPTCHA) und Soft-404s per Body-Signatur.

**Erkennung per Body-Signatur, nie per Statuscode.** Das ist der ganze Sinn von
N01: die Consent-Wall kommt mit HTTP 200, eine Statuscode-Pruefung faengt
nichts. Bei einer Blockade wirft der Aufrufer ``Blocked(reason)``; der Runner
(E0.9) hat den Snapshot da schon persistiert (er ist Beweismaterial), ruft aber
keinen Extraktor auf und stoppt die ganze Domain.

Ein Soft-404 ist das Gegenstueck (N02): Status 200 mit Fehlerinhalt. Anders als
die Blockade bricht er die Domain nicht ab, er eskaliert nur. **F1:**
``books.toscrape.com`` beendet seine Pagination mit einem *echten* HTTP 404
(``/catalogue/page-51.html``) — das ist der Terminator, kein Soft-404 und kein
Incident. ``detect_soft_404`` prueft deshalb ausschliesslich Status-200-Koerper
und gibt fuer alles andere ``None`` zurueck.

Beide Detektoren lesen **eine** versionierte Datei ``signatures.yaml`` mit
``schema_version`` und den zwei Listen ``block_signatures`` und
``soft_404_signatures``. Diese Datei ist der einzige Ort, an dem Signaturen
stehen — die Phase-1-Fetcher-Vorpruefung und die Phase-2-Heiler-Vorpruefung
duerfen sich nie darueber uneinig sein, was „blockiert" heisst. Site-Packs
erweitern (oder ersetzen) die Listen ueber ``fetch.block_signatures`` /
``fetch.soft_404_signatures``; die Overrides tragen dasselbe Entry-Modell, ein
schlechtes Pack scheitert also an der statischen Validierung, nicht erst im Fetch.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator
from selectolax.lexbor import LexborHTMLParser

_SIGNATURES_PATH = Path(__file__).with_name("signatures.yaml")

# Unter so viel extrahiertem Text ist eine 200-Antwort verdaechtig leer.
_TEXT_FLOOR_BYTES = 200

SignatureScope = Literal["body", "title", "header"]
SignatureKind = Literal["regex", "css"]


class Blocked(Exception):
    """Die Antwort ist eine Blockade (Consent-Wall/Challenge/CAPTCHA)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SignatureEntry(_Frozen):
    """Eine einzelne Signatur. ``pattern`` ist ein CSS-Selektor oder ein Regex.

    ``scope`` waehlt den Heuhaufen: ``body`` = ganzer HTML-Text, ``title`` =
    Titeltext, ``header`` = ``k: v``-Zeilen der Antwort-Header (klein
    geschrieben). Fuer ``kind: css`` ist ``scope`` per Konvention ``body``.
    ``source_note`` belegt, woher die Signatur stammt (Provenienz, kein Freitext-
    Kommentar) — sie verrottet sonst.
    """

    id: str
    pattern: str
    scope: SignatureScope
    kind: SignatureKind
    source_note: str

    @model_validator(mode="after")
    def _compilable(self) -> SignatureEntry:
        if self.kind == "regex":
            try:
                re.compile(self.pattern)
            except re.error as exc:  # pragma: no cover - defensiv
                raise ValueError(f"Signatur {self.id!r}: kein gueltiger Regex: {exc}") from exc
        return self


class SignatureOverride(_Frozen):
    """Pack-Override einer Signaturliste. Default ist *anhaengen*, ``replace``
    tauscht die globale Liste ganz aus."""

    replace: bool = False
    signatures: list[SignatureEntry]


class SignatureFile(_Frozen):
    """Die versionierte Signaturdatei: eine Version, zwei Listen."""

    schema_version: int
    block_signatures: list[SignatureEntry]
    soft_404_signatures: list[SignatureEntry]

    @model_validator(mode="after")
    def _unique_ids(self) -> SignatureFile:
        seen: set[str] = set()
        for entry in (*self.block_signatures, *self.soft_404_signatures):
            if entry.id in seen:
                raise ValueError(f"Doppelte Signatur-id: {entry.id!r}")
            seen.add(entry.id)
        return self


@dataclass(frozen=True)
class SignatureHit:
    """Das Ergebnis einer Erkennung: die erste passende Signatur.

    Traegt ``id`` und ``pattern`` (die der Incident-Writer ins Evidence-Dict
    schreibt) plus ``scope``/``kind``. Strukturelle Treffer (meta-refresh,
    Text-Floor) stehen nicht in der YAML und tragen ``kind='structural'``.
    """

    id: str
    pattern: str
    scope: str
    kind: str

    @classmethod
    def from_entry(cls, entry: SignatureEntry) -> SignatureHit:
        return cls(id=entry.id, pattern=entry.pattern, scope=entry.scope, kind=entry.kind)


@lru_cache(maxsize=8)
def load_signature_file(path: str | None = None) -> SignatureFile:
    """Laedt und validiert ``signatures.yaml`` (fehlendes ``schema_version`` oder
    doppelte ids sind ein Ladefehler)."""
    target = Path(path) if path else _SIGNATURES_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    return SignatureFile.model_validate(data)


def block_signatures() -> tuple[SignatureEntry, ...]:
    """Die globalen Block-Signaturen."""
    return tuple(load_signature_file().block_signatures)


def soft_404_signatures() -> tuple[SignatureEntry, ...]:
    """Die globalen Soft-404-Signaturen."""
    return tuple(load_signature_file().soft_404_signatures)


def _resolve(
    base: tuple[SignatureEntry, ...], override: SignatureOverride | None
) -> tuple[SignatureEntry, ...]:
    if override is None:
        return base
    if override.replace:
        return tuple(override.signatures)
    return base + tuple(override.signatures)


def resolve_block_signatures(
    override: SignatureOverride | None,
) -> tuple[SignatureEntry, ...]:
    """Globale Block-Liste plus Pack-Override (anhaengen, oder ersetzen)."""
    return _resolve(block_signatures(), override)


def resolve_soft_404_signatures(
    override: SignatureOverride | None,
) -> tuple[SignatureEntry, ...]:
    """Globale Soft-404-Liste plus Pack-Override (anhaengen, oder ersetzen)."""
    return _resolve(soft_404_signatures(), override)


def _title_text(tree: LexborHTMLParser) -> str:
    node = tree.css_first("title")
    return node.text() if node is not None else ""


def _header_haystack(headers: Mapping[str, str]) -> str:
    # Header-Namen und -Werte klein geschrieben: `cf-mitigated` matcht die
    # Praesenz des Headers unabhaengig von der Schreibweise.
    return "\n".join(f"{k}: {v}" for k, v in headers.items()).lower()


def _matches(
    entry: SignatureEntry, *, tree: LexborHTMLParser, html: str, headers_text: str
) -> bool:
    if entry.kind == "css":
        return tree.css_first(entry.pattern) is not None
    if entry.scope == "title":
        haystack = _title_text(tree)
    elif entry.scope == "header":
        haystack = headers_text
    else:
        haystack = html
    return re.search(entry.pattern, haystack) is not None


def detect_block(
    html: str,
    headers: Mapping[str, str],
    signatures: Sequence[SignatureEntry] | None = None,
) -> SignatureHit | None:
    """Gibt die erste passende Block-Signatur zurueck, sonst ``None``.

    Erst die Signaturliste (Header-, CSS-, Titel-, Body-Signaturen in
    Listenreihenfolge), dann zwei strukturelle Pruefungen, die sich nicht als
    einfaches Pattern ausdruecken lassen: ``<meta http-equiv=refresh>`` auf einen
    Consent-Pfad und der Text-Floor (eine 200-Antwort mit fast keinem Text).
    """
    entries = block_signatures() if signatures is None else signatures
    tree = LexborHTMLParser(html)
    headers_text = _header_haystack(headers)

    for entry in entries:
        if _matches(entry, tree=tree, html=html, headers_text=headers_text):
            return SignatureHit.from_entry(entry)

    for meta in tree.css("meta"):
        if (meta.attributes.get("http-equiv") or "").lower() != "refresh":
            continue
        target = (meta.attributes.get("content") or "").lower()
        if "consent" in target or "cookie" in target:
            return SignatureHit(
                id="meta_refresh_consent",
                pattern="meta[http-equiv=refresh] -> consent/cookie",
                scope="body",
                kind="structural",
            )

    body_node = tree.body
    extracted = body_node.text(separator=" ", strip=True) if body_node is not None else ""
    if len(extracted.encode("utf-8")) < _TEXT_FLOOR_BYTES:
        return SignatureHit(
            id="empty_below_text_floor",
            pattern=f"body text < {_TEXT_FLOOR_BYTES} bytes",
            scope="body",
            kind="structural",
        )

    return None


def detect_soft_404(
    html: str,
    http_status: int,
    url: str,
    signatures: Sequence[SignatureEntry] | None = None,
) -> SignatureHit | None:
    """Gibt die erste passende Soft-404-Signatur zurueck, sonst ``None``.

    **Nur fuer Status 200.** Ein echter HTTP 404 (F1: der Pagination-Terminator
    von books.toscrape.com) ist kein Soft-404 — fuer jeden Nicht-200-Status ist
    das Ergebnis ``None``. ``url`` steht dem Aufrufer fuers Evidence-Dict zur
    Verfuegung; die reine Erkennung braucht ihn nicht.
    """
    del url  # nur fuer die Aufrufer-Evidence, nicht fuer die Erkennung
    if http_status != 200:
        return None
    entries = soft_404_signatures() if signatures is None else signatures
    tree = LexborHTMLParser(html)
    headers_text = ""
    for entry in entries:
        if _matches(entry, tree=tree, html=html, headers_text=headers_text):
            return SignatureHit.from_entry(entry)
    return None
