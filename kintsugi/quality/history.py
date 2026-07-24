"""HistoryStats: der 14-Tage-Median als Baseline fuer die Detektoren.

docs/03-data-model.md §Laeufe und docs/09-decisions.md ADR-002. Der Median ist
auf ``(domain, entity)`` ueber **alle** Pack-Versionen gefasst, nicht auf
``site_pack_id`` — sonst meldete jeder Promote auf genau den drei Laeufen danach
``insufficient_baseline``, im Fenster, in dem Erkennung am meisten zaehlt.

``MIN_QUALIFYING_RUNS`` Laeufe muessen im Fenster liegen, sonst ist die Baseline
unzureichend (``median_14d=None``). ``load_history`` (I1.1.3) fuellt das aus der
Datenbank; die Detektoren (``compute_profile``, I1.1.2) nehmen es als reinen Input.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["MIN_QUALIFYING_RUNS", "HistoryStats"]

MIN_QUALIFYING_RUNS = 3


@dataclass(frozen=True)
class HistoryStats:
    """Baseline-Statistik eines (domain, entity) ueber die letzten 14 Tage."""

    median_14d: int | None
    fill_rate_median: dict[str, float] = field(default_factory=dict)
    qualifying_runs: int = 0

    @property
    def insufficient_baseline(self) -> bool:
        return self.qualifying_runs < MIN_QUALIFYING_RUNS
