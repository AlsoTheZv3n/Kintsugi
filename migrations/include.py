"""Gemeinsame Drift-Waechter-Konfiguration fuer env.py und den Round-Trip-Test.

Bewusst hier und nicht in env.py: env.py fasst zur Importzeit ``context.config``
an und laesst sich nur innerhalb der Alembic-Laufzeit importieren. Dieses Modul
ist frei importierbar, damit der Test denselben ``include_object``-Hook und
dieselbe Allowlist nutzt statt sie zu duplizieren.
"""

from __future__ import annotations

# Partielle Unique- und Ausdrucks-Indizes runden als reflektierte
# Ausdrucksstrings zurueck, die selten wortgleich zum SQLAlchemy-Konstrukt
# passen. Ohne Ausschluss meldete Autogenerate sie als Dauerdrift. Jeder
# Eintrag nennt die Migration, die ihn anlegt.
INDEX_ALLOWLIST: dict[str, str] = {
    "site_pack_one_active": "0002_site_pack",
    "site_pack_one_canary": "0002_site_pack",
    "snapshot_golden": "0003_run_snapshot",
    "record_current": "0004_record_gold",
    "incident_open": "0005_incident",
    "incident_open_dedup": "0005_incident",
}


def include_object(
    obj: object, name: str | None, type_: str, _reflected: bool, _compare_to: object
) -> bool:
    """False fuer die allowgelisteten partiellen Indizes, sonst True."""
    return not (type_ == "index" and name in INDEX_ALLOWLIST)
