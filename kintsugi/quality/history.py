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
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy import Connection

__all__ = ["MIN_QUALIFYING_RUNS", "HistoryStats", "load_history"]

MIN_QUALIFYING_RUNS = 3

# Der Median ist auf (domain, entity) ueber ALLE Pack-Versionen gefasst (ADR-002).
# status/trigger-Filter: canary laeuft auf einem Bruchteil der URLs und zoege den
# Median gegen null; replay und failed sind keine echten Produktionslaeufe.
_COUNT_MEDIAN_SQL = text(
    """
    SELECT count(*) AS n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY r.rows_extracted) AS median_rows
    FROM run r JOIN site_pack sp ON sp.id = r.site_pack_id
    WHERE sp.domain = :domain AND sp.entity = :entity
      AND r.started_at >= :cutoff
      AND r.status IN ('ok', 'degraded')
      AND r.trigger IN ('schedule', 'manual')
    """
)

_FILL_RATE_MEDIAN_SQL = text(
    """
    SELECT f.key AS field,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY (f.value)::float) AS median
    FROM run r JOIN site_pack sp ON sp.id = r.site_pack_id
      CROSS JOIN LATERAL jsonb_each_text(r.metrics -> 'quality' -> 'fill_rate') AS f
    WHERE sp.domain = :domain AND sp.entity = :entity
      AND r.started_at >= :cutoff
      AND r.status IN ('ok', 'degraded')
      AND r.trigger IN ('schedule', 'manual')
    GROUP BY f.key
    """
)


@dataclass(frozen=True)
class HistoryStats:
    """Baseline-Statistik eines (domain, entity) ueber die letzten 14 Tage."""

    median_14d: int | None
    fill_rate_median: dict[str, float] = field(default_factory=dict)
    qualifying_runs: int = 0

    @property
    def insufficient_baseline(self) -> bool:
        return self.qualifying_runs < MIN_QUALIFYING_RUNS


def load_history(conn: Connection, domain: str, entity: str, now: datetime) -> HistoryStats:
    """Die 14-Tage-Baseline aus der Datenbank (der einzige DB-Teil von E1.1).

    Unter ``MIN_QUALIFYING_RUNS`` qualifizierenden Laeufen ist die Baseline
    unzureichend: ``median_14d=None`` und ein leerer Feld-Median. Der
    ``site_pack_id``-Scope wird bewusst gemieden — nach jedem Promote haette die
    neue Version keine Historie und meldete genau im Beobachtungsfenster
    ``insufficient_baseline``.
    """
    params = {"domain": domain, "entity": entity, "cutoff": now - timedelta(days=14)}
    row = conn.execute(_COUNT_MEDIAN_SQL, params).one()
    qualifying = int(row.n)
    if qualifying < MIN_QUALIFYING_RUNS:
        return HistoryStats(median_14d=None, fill_rate_median={}, qualifying_runs=qualifying)

    fill_rate_median = {
        r.field: float(r.median) for r in conn.execute(_FILL_RATE_MEDIAN_SQL, params)
    }
    median_rows = None if row.median_rows is None else round(row.median_rows)
    return HistoryStats(
        median_14d=median_rows, fill_rate_median=fill_rate_median, qualifying_runs=qualifying
    )
