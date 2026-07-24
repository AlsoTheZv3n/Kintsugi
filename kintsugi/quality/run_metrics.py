"""Das ``run.metrics``-Gesamtdokument in der namespaced Form (#82).

Phase 0 schreibt ``RunCounters`` (13 Betriebs-Schluessel), Phase 1 ``QualityProfile``
(11 Qualitaets-Schluessel) in **dieselbe** Spalte ``run.metrics jsonb``. Die
Entscheidung (Issue #82): zwei getrennte, benannte Bloecke —
``{"counters": {...}, "quality": {...}}`` — je ein Modell mit ``extra="forbid"``,
keine Kollision auf den geteilten Schluesseln ``http``, ``fetch_ms_p95``,
``rows_considered``. So bleiben Betrieb und Qualitaet fuer den Phase-3-Exporter
getrennt validierbar. docs/03 §Laeufe haelt fest, dass diese Form bindend ist.

``quality`` ist optional: der Phase-0-Runner schreibt vor der Quality-Schicht
(I1.1.4) nur den ``counters``-Block; sobald das Profil berechnet wird, kommt
``quality`` hinzu. Genau **ein** Exact-Key-Set-Beleg lebt im Repo: das Golden
``tests/unit/golden/run_metrics.json``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from kintsugi.quality.profile import QualityProfile

__all__ = ["CountersBlock", "RunMetrics"]


class CountersBlock(BaseModel):
    """Spiegelt ``RunCounters.to_metrics()`` — die 13 Betriebs-Schluessel."""

    model_config = ConfigDict(extra="forbid")

    urls_discovered: int
    pages_fetched: int
    pages_skipped_robots: int
    pages_unchanged: int
    http: dict[str, int]
    rows_considered: int
    rows_extracted: int
    rows_valid: int
    rows_rejected: dict[str, int]
    rows_inserted: int
    rows_versioned: int
    rows_unchanged: int
    fetch_ms_p95: float


class RunMetrics(BaseModel):
    """Das vollstaendige ``run.metrics``-Dokument: benannte Betriebs-/Qualitaetsbloecke."""

    model_config = ConfigDict(extra="forbid")

    counters: CountersBlock
    quality: QualityProfile | None = None
