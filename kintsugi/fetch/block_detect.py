"""Erkennt Consent-Walls, Bot-Challenges und CAPTCHAs (README §Compliance, N01).

**Erkennung per Body-Signatur, nie per Statuscode.** Das ist der ganze Sinn von
N01: die Consent-Wall kommt mit HTTP 200. Eine Statuscode-Pruefung faengt
nichts. Bei einem Treffer wirft der Aufrufer ``Blocked(reason)``; der Runner
(E0.9) hat den Snapshot da schon persistiert (er ist Beweismaterial), ruft aber
keinen Extraktor auf und stoppt die ganze Domain.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from selectolax.lexbor import LexborHTMLParser

_SIGNATURES_PATH = Path(__file__).with_name("signatures.yaml")

# Unter so viel extrahiertem Text ist eine 200-Antwort verdaechtig leer.
_TEXT_FLOOR_BYTES = 200


class Blocked(Exception):
    """Die Antwort ist eine Blockade (Consent-Wall/Challenge/CAPTCHA)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Signature:
    name: str
    kind: str  # css | substring | regex
    value: str


@lru_cache(maxsize=1)
def load_signatures(path: str | None = None) -> tuple[Signature, ...]:
    """Laedt und validiert die Signaturliste; lehnt doppelte Namen ab."""
    target = Path(path) if path else _SIGNATURES_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    signatures: list[Signature] = []
    seen: set[str] = set()
    for entry in data["signatures"]:
        name = entry["name"]
        if name in seen:
            raise ValueError(f"Doppelter Signatur-Name in {target}: {name!r}")
        seen.add(name)
        signatures.append(Signature(name=name, kind=entry["kind"], value=entry["value"]))
    return tuple(signatures)


def detect(body: bytes, headers: Mapping[str, str], encoding: str = "utf-8") -> str | None:
    """Gibt den Namen der ersten passenden Signatur zurueck, sonst None."""
    # Response-Header: Cloudflare markiert abgemilderte Antworten.
    if "cf-mitigated" in {k.lower() for k in headers}:
        return "cf_mitigated_header"

    text = body.decode(encoding, errors="replace")
    tree = LexborHTMLParser(text)

    for sig in load_signatures():
        if sig.kind == "css" and tree.css_first(sig.value) is not None:
            return sig.name
        if sig.kind == "substring" and sig.value in text:
            return sig.name
        if sig.kind == "regex" and re.search(sig.value, text):
            return sig.name

    # <meta http-equiv="refresh"> auf einen Consent-Pfad.
    for meta in tree.css("meta"):
        if (meta.attributes.get("http-equiv") or "").lower() != "refresh":
            continue
        target = (meta.attributes.get("content") or "").lower()
        if "consent" in target or "cookie" in target:
            return "meta_refresh_consent"

    # Text-Floor: eine 200-Antwort mit fast keinem Text ist verdaechtig.
    body_node = tree.body
    extracted = body_node.text(separator=" ", strip=True) if body_node is not None else ""
    if len(extracted.encode("utf-8")) < _TEXT_FLOOR_BYTES:
        return "empty_below_text_floor"

    return None
