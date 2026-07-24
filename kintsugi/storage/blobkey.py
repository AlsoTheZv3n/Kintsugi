"""Inhaltsadressierter Schluessel fuer Bronze-Blobs.

docs/03-data-model.md, Abschnitt Bronze, legt das Format fest:
``blob_key text NOT NULL, -- raw/<domain>/<yyyy>/<mm>/<sha256>.gz``.

Der Schluessel ist eine **POSIX-Zeichenkette mit Vorwaertsschraegstrichen**,
gebaut ausschliesslich mit ``PurePosixPath`` — nie mit den plattformabhaengigen
Pfad-Helfern aus ``os`` und ``pathlib``, die auf diesem Windows-Host (F6)
Rueckwaertsschraegstriche erzeugten und Schluessel liefern wuerden, die beim
Umzug auf SeaweedFS in Phase 5 brechen. Der Dateisystem-Writer bildet den
POSIX-Schluessel selbst auf einen echten Pfad ab; der gespeicherte Wert bleibt
kanonisch.

Die Domain wird an genau einer Stelle aus der URL abgeleitet (``domain_of``);
jedes andere Modul importiert diesen Helfer, statt ein zweites Mal ``urlsplit``
fuer Domain-Zwecke aufzurufen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import urlsplit

__all__ = ["blob_key_for_domain", "build_blob_key", "domain_of"]


def domain_of(url: str) -> str:
    """Kleingeschriebener Hostname der URL, ohne Port und ohne Userinfo.

    IDN bleibt so kodiert, wie es in der URL steht — Punycode wird hier nicht
    aufgeloest, damit derselbe Host immer denselben Segmentwert ergibt.
    """
    host = urlsplit(url).hostname
    if not host:
        raise ValueError(f"URL ohne Host, kein blob_key ableitbar: {url!r}")
    return host.lower()


def build_blob_key(url: str, content_hash: bytes, fetched_at: datetime) -> str:
    """Baut ``raw/<domain>/<yyyy>/<mm>/<sha256hex>.gz``.

    ``fetched_at`` wird nach UTC umgerechnet (ADR-009 Kontrakt 4), bevor Jahr
    und Monat gebildet werden: ein Lauf kurz vor Mitternacht darf nicht je nach
    lokaler Zone in zwei Monate fallen. Diese Maschine laeuft in einer
    Nicht-UTC-Zone, ein naives ``datetime.now()`` waere also eine echte Gefahr
    — naive Zeitstempel werden abgelehnt.

    Der Monat ist eine Erstschreib-Partition, nicht Teil der Identitaet: der
    Blob-Writer schlaegt einen vorhandenen ``blob_key`` ueber ``content_hash``
    nach (Index ``snapshot_hash``, docs/03 §Bronze) und verwendet ihn wieder.
    Sonst laege identischer Inhalt vom 31. Januar und 1. Februar doppelt unter
    zwei ``<yyyy>/<mm>``-Praefixen.
    """
    return blob_key_for_domain(domain_of(url), content_hash, fetched_at)


def blob_key_for_domain(domain: str, content_hash: bytes, fetched_at: datetime) -> str:
    """Wie ``build_blob_key``, aber mit bereits abgeleiteter Domain.

    Der Snapshot-Store (``kintsugi/storage/snapshots.py``) hat die Domain schon
    zur Hand und braucht ``urlsplit`` nicht erneut. Das Schluesselformat lebt
    an genau dieser Stelle, damit Store und Fetch nie auseinanderdriften.
    """
    if fetched_at.tzinfo is None:
        raise ValueError(
            "fetched_at ist zeitzonenlos. blob_key braucht einen UTC-Bezug, "
            "sonst kippt die Monatspartition je nach lokaler Zone."
        )
    if len(content_hash) != 32:
        raise ValueError(f"content_hash muss 32 sha256-Rohbytes sein, nicht {len(content_hash)}")

    moment = fetched_at.astimezone(UTC)
    key = PurePosixPath(
        "raw",
        domain,
        f"{moment.year:04d}",
        f"{moment.month:02d}",
        f"{content_hash.hex()}.gz",
    )
    return key.as_posix()
