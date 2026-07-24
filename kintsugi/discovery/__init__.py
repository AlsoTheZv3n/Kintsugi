"""URL-Discovery: getrennt vom Fetch, weil separat heilbar (docs/01 §Discovery).

Die konkreten Strategien werden per Seiteneffekt-Import registriert — wie bei
``kintsugi.transform`` und ``kintsugi.extract``. Wer ``get_strategy`` aufruft,
importiert dieses Paket und bekommt eine vollstaendig gefuellte Registry.
"""

from __future__ import annotations

# Jede Strategie wird genau einmal registriert; die Reihenfolge ist bedeutungslos.
from kintsugi.discovery import pagination as _pagination  # noqa: F401  (Registrierung)
from kintsugi.discovery import seed_list as _seed_list  # noqa: F401  (Registrierung)
from kintsugi.discovery import stubs as _stubs  # noqa: F401  (Registrierung)
from kintsugi.discovery.base import (
    REGISTRY,
    DiscoveryContext,
    DiscoveryStrategy,
    get_strategy,
    register,
)

__all__ = [
    "REGISTRY",
    "DiscoveryContext",
    "DiscoveryStrategy",
    "get_strategy",
    "register",
]
