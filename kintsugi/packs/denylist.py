"""Ladezeit-Sperren fuer verbotene Ziele und Zugangsdaten.

docs/01 §Nicht-Ziele: kein Zugriff hinter Logins, kein Wettruesten gegen
Enterprise-Bot-Schutz. docs/07 §Nicht anfassen nennt die Ziele. Bisher war beides
Prosa; hier wird es zu Ladefehlern. Beide Pruefungen laufen in der
Modellvalidierung, sodass auch ein Heiler-Vorschlag in Phase 2 sie kostenlos
trifft — vor dem Golden-Fixture-Gate.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

# Marken-Labels von der docs/07-„Nicht anfassen"-Liste. Geprueft wird jedes
# Label der Domain, damit alle TLDs (amazon.de) und Subdomains
# (de.linkedin.com) erfasst sind — ein blosser Substring-Vergleich reichte nicht.
DENIED_BRANDS: frozenset[str] = frozenset(
    {
        "amazon",  # docs/07 §Nicht anfassen: Amazon
        "linkedin",  # docs/07 §Nicht anfassen: LinkedIn
        "instagram",  # docs/07 §Nicht anfassen: Instagram
        "facebook",  # docs/07 §Nicht anfassen: Meta-Umfeld von Instagram
        "ticketmaster",  # docs/07 §Nicht anfassen: Ticketplattformen
        "eventim",  # docs/07 §Nicht anfassen: Ticketplattformen
        "stubhub",  # docs/07 §Nicht anfassen: Ticketplattformen
    }
)

# Schluesselnamen, die auf eingeschmuggelte Zugangsdaten hindeuten.
_CREDENTIAL_RE = re.compile(
    r"auth|authorization|login|password|cookie|session|credential|bearer|api_key",
    re.IGNORECASE,
)


class DeniedTargetError(Exception):
    """Die Zieldomain steht auf der docs/07-„Nicht anfassen"-Liste."""


class CredentialInPackError(Exception):
    """Ein Schluessel im Pack deutet auf Zugangsdaten hin."""


def _normalise(domain: str) -> str:
    text = domain.strip().rstrip(".").lower()
    try:
        return text.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return text


def check_domain(domain: str) -> None:
    """Wirft ``DeniedTargetError``, wenn ein Domain-Label auf der Liste steht."""
    for label in _normalise(domain).split("."):
        if label in DENIED_BRANDS:
            raise DeniedTargetError(
                f"Domain {domain!r} steht auf der docs/07-'Nicht anfassen'-Liste (Marke {label!r})"
            )


def check_no_credentials(data: object, path: str = "") -> None:
    """Durchsucht das geparste Dokument rekursiv nach Zugangsdaten-Schluesseln.

    Ergaenzt ``extra='forbid'``: das faengt Tippfehler in bekannten Bloecken,
    diese Pruefung faengt bewusste Eintraege in dict-typisierten Feldern.
    """
    if isinstance(data, Mapping):
        for key, value in data.items():
            key_str = str(key)
            here = f"{path}.{key_str}" if path else key_str
            if _CREDENTIAL_RE.search(key_str):
                raise CredentialInPackError(
                    f"Schluessel {here!r} deutet auf Zugangsdaten hin — Site-Packs "
                    "duerfen keine Authentifizierung tragen (docs/01 §Nicht-Ziele)"
                )
            check_no_credentials(value, here)
    elif isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        for index, item in enumerate(data):
            check_no_credentials(item, f"{path}[{index}]")
