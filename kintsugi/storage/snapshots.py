"""SnapshotStore: die Naht zwischen Bronze-Blobs und ihrem Ablageort.

docs/03-data-model.md §Bronze und docs/08-roadmap.md §Phase 0 ("Snapshot-
Persistenz, zunaechst Dateisystem statt Objektspeicher"). Das ``Protocol`` ist
genau die Stelle, die SeaweedFS in Phase 5 (docs/08) ersetzt — nichts oberhalb
davon darf die Dateisystem-Implementierung direkt importieren.

Schluessel sind **POSIX-Zeichenketten** (Vorwaertsschraegstriche), gebaut in
``kintsugi.storage.blobkey``. Der Filesystem-Writer bildet den Schluessel selbst
auf einen echten Pfad ab; der gespeicherte Wert bleibt kanonisch, damit er beim
Umzug auf einen Objektspeicher unveraendert traegt. Der Store haelt sich an die
Standardbibliothek (gzip, os, pathlib, tempfile) — keine Drittabhaengigkeit.
"""

from __future__ import annotations

import gzip
import os
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

from kintsugi.storage.blobkey import blob_key_for_domain

__all__ = ["FilesystemSnapshotStore", "SnapshotStore"]


@runtime_checkable
class SnapshotStore(Protocol):
    """Inhaltsadressierter Bronze-Speicher. Phase-5-Naht fuer SeaweedFS."""

    def put(self, blob_key: str, body: bytes) -> None:
        """Schreibt ``body`` unter ``blob_key``. No-op, wenn der Schluessel existiert."""
        ...

    def get(self, blob_key: str) -> bytes:
        """Liefert den unkomprimierten Body zu ``blob_key``."""
        ...

    def exists(self, blob_key: str) -> bool:
        """Ob ``blob_key`` bereits abgelegt ist."""
        ...

    def build_key(self, domain: str, fetched_at: datetime, content_hash: bytes) -> str:
        """Baut den kanonischen ``raw/<domain>/<yyyy>/<mm>/<sha256hex>.gz``-Schluessel."""
        ...


def _safe_parts(blob_key: str) -> tuple[str, ...]:
    """Zerlegt einen Schluessel in Segmente und lehnt Ausbruchsversuche ab.

    Ein Blob-Schluessel ist ein relativer POSIX-Pfad. Ein Rueckwaertsschraeg-
    strich, ein fuehrender Schraegstrich, ein Laufwerksbuchstabe oder ein
    ``..``-Segment koennte aus ``snapshot_root`` ausbrechen — das waere ein
    Pfad-Traversal-Schreibzugriff und wird hart abgelehnt.
    """
    if "\\" in blob_key:
        raise ValueError(f"blob_key enthaelt einen Backslash: {blob_key!r}")
    if blob_key.startswith("/"):
        raise ValueError(f"blob_key ist absolut (fuehrender '/'): {blob_key!r}")
    if len(blob_key) >= 2 and blob_key[1] == ":":
        raise ValueError(f"blob_key traegt einen Laufwerksbuchstaben: {blob_key!r}")
    parts = PurePosixPath(blob_key).parts
    if ".." in parts:
        raise ValueError(f"blob_key enthaelt ein '..'-Segment: {blob_key!r}")
    if not parts:
        raise ValueError("blob_key ist leer")
    return parts


class FilesystemSnapshotStore:
    """gzip-Blobs unter ``Settings.snapshot_root``, atomar geschrieben."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)

    def build_key(self, domain: str, fetched_at: datetime, content_hash: bytes) -> str:
        return blob_key_for_domain(domain, content_hash, fetched_at)

    def _path(self, blob_key: str) -> Path:
        return self._root.joinpath(*_safe_parts(blob_key))

    def exists(self, blob_key: str) -> bool:
        return self._path(blob_key).is_file()

    def get(self, blob_key: str) -> bytes:
        return gzip.decompress(self._path(blob_key).read_bytes())

    def put(self, blob_key: str, body: bytes) -> None:
        path = self._path(blob_key)
        # Inhaltsadressiert: gleicher Schluessel = gleicher Inhalt. Ein zweites
        # put ist ein No-op, kein Rewrite — sonst wackelten mtime/inode und ein
        # halb geschriebener Blob koennte kurz sichtbar werden.
        if path.is_file():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        # mtime=0 macht den gzip-Header reproduzierbar: identischer Body ergibt
        # identische Bytes, was fuer Golden-Fixtures und Diffs zaehlt.
        compressed = gzip.compress(body, mtime=0)
        # Atomar: temp im *selben* Verzeichnis schreiben, fsync, dann os.replace.
        # Ein halb geschriebener Blob darf nie beobachtbar sein — Bronze ist die
        # Basis fuer Golden-Fixtures und Diffs.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(compressed)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            # Aufraeumen, falls os.replace nie lief — kein temp-Leichnam bleibt.
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
