"""RunCounters: die Zaehler, die der Runner in ``run.metrics`` serialisiert.

docs/03-data-model.md §Laeufe (das ``metrics``-JSON) und docs/06-observability.md
§Metriken. Feldnamen decken sich mit den Prometheus-Namen aus docs/06
(``kintsugi_pages_fetched_total``, ``kintsugi_rows_extracted_total``), damit
Phase 3 sie ohne Umbenennen exportieren kann.

Die tragende Entscheidung ist der **considered-vs-written**-Split (docs/02
§Beispiel, ``min_rows_per_run``): ``rows_considered`` zaehlt jeden Natural Key,
den der Lauf aufgeloest hat — auch die, die der unchanged/304-Kurzschluss
uebersprungen hat (die zaehlen zugleich in ``rows_unchanged``). ``min_rows_per_run``
wird **inklusiv** und gegen betrachtete Zeilen geprueft
(``rows_valid + rows_unchanged >= min``), nie gegen ``rows_inserted`` — sonst
meldet der zweite Lauf ueber eine statische Seite null geschriebene Zeilen und
loest faelschlich einen ``row_count_anomaly``-Incident und ``failed`` aus, genau
den Fehler, den die DoD fangen soll.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

__all__ = ["RunCounters"]


def _p95(samples: list[float]) -> float:
    """95. Perzentil (naechster Rang), 0.0 bei leerer Stichprobe. Kein numpy."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = math.ceil(0.95 * len(ordered))  # 1-basiert
    return ordered[max(rank - 1, 0)]


@dataclass
class RunCounters:
    """Vom Runner mutiert, beim Schliessen des Laufs nach ``run.metrics`` serialisiert."""

    urls_discovered: int = 0
    pages_fetched: int = 0
    pages_skipped_robots: int = 0
    pages_unchanged: int = 0
    http: dict[int, int] = field(default_factory=dict)
    rows_considered: int = 0
    rows_extracted: int = 0
    rows_valid: int = 0
    rows_rejected: dict[str, int] = field(default_factory=dict)
    rows_inserted: int = 0
    rows_versioned: int = 0
    rows_unchanged: int = 0
    _fetch_ms: list[float] = field(default_factory=list, repr=False)

    def record_http(self, status: int, *, fetch_ms: float | None = None) -> None:
        """Ein tatsaechlich abgerufener HTTP-Status (auch 304/404).

        ``pages_fetched`` und die ``http``-Summe bleiben so per Konstruktion
        gleich (docs/06). Von robots uebersprungene URLs erzeugen keinen
        HTTP-Verkehr und laufen ueber ``skip_robots`` statt hier.
        """
        self.http[status] = self.http.get(status, 0) + 1
        self.pages_fetched += 1
        if fetch_ms is not None:
            self._fetch_ms.append(fetch_ms)

    def skip_robots(self) -> None:
        self.pages_skipped_robots += 1

    def reject(self, reason: str) -> None:
        self.rows_rejected[reason] = self.rows_rejected.get(reason, 0) + 1

    @property
    def fetch_ms_p95(self) -> float:
        return _p95(self._fetch_ms)

    def meets_min_rows(self, min_rows: int) -> bool:
        """Inklusiver Schwellenvergleich gegen betrachtete Zeilen (docs/02)."""
        return (self.rows_valid + self.rows_unchanged) >= min_rows

    def to_metrics(self) -> dict[str, object]:
        """Der ``run.metrics``-JSON-Beleg. Nur JSON-serialisierbare Typen."""
        return {
            "urls_discovered": self.urls_discovered,
            "pages_fetched": self.pages_fetched,
            "pages_skipped_robots": self.pages_skipped_robots,
            "pages_unchanged": self.pages_unchanged,
            # JSON-Objektschluessel sind Strings; HTTP-Status als String.
            "http": {str(status): count for status, count in sorted(self.http.items())},
            "rows_considered": self.rows_considered,
            "rows_extracted": self.rows_extracted,
            "rows_valid": self.rows_valid,
            "rows_rejected": dict(sorted(self.rows_rejected.items())),
            "rows_inserted": self.rows_inserted,
            "rows_versioned": self.rows_versioned,
            "rows_unchanged": self.rows_unchanged,
            "fetch_ms_p95": self.fetch_ms_p95,
        }
