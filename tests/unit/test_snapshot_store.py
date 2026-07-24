"""FilesystemSnapshotStore: Round-Trip, Atomaritaet, Schluesselform (I0.9.1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from kintsugi.storage.snapshots import FilesystemSnapshotStore, SnapshotStore

FETCHED = datetime(2026, 2, 9, 23, 59, tzinfo=UTC)
HASH = bytes(range(32))  # 32 Rohbytes -> 64 Hex-Zeichen


def test_roundtrip_grosser_und_nicht_ascii_body(tmp_path):
    store = FilesystemSnapshotStore(tmp_path)
    big = b"A" * (1024 * 1024 + 7)  # > 1 MiB
    key_big = store.build_key("books.toscrape.com", FETCHED, HASH)
    store.put(key_big, big)
    assert store.get(key_big) == big

    unicode_body = "Ærøskøbing naïve café 日本語 ☕".encode()
    key_u = "raw/books.toscrape.com/2026/02/" + ("ab" * 32) + ".gz"
    store.put(key_u, unicode_body)
    assert store.get(key_u) == unicode_body


def test_datei_ist_gzip_und_kein_temp_leichnam(tmp_path):
    store = FilesystemSnapshotStore(tmp_path)
    key = store.build_key("books.toscrape.com", FETCHED, HASH)
    store.put(key, b"hallo welt")

    on_disk = tmp_path.joinpath(*key.split("/"))
    assert on_disk.read_bytes()[:2] == b"\x1f\x8b"  # gzip-Magic
    gz_files = list(tmp_path.rglob("*.gz"))
    tmp_files = list(tmp_path.rglob("*.tmp"))
    assert len(gz_files) == 1
    assert tmp_files == []


def test_build_key_form():
    store = FilesystemSnapshotStore("/anywhere")
    key = store.build_key("books.toscrape.com", FETCHED, HASH)
    assert key == "raw/books.toscrape.com/2026/02/" + HASH.hex() + ".gz"
    assert key.count("/") == 4
    tail = key.rsplit("/", 1)[-1]
    assert len(tail) == 64 + len(".gz")


@pytest.mark.parametrize(
    ("bad", "match"),
    [
        ("raw/../etc/passwd.gz", r"\.\."),
        ("/etc/passwd.gz", "absolut"),
        ("raw\\books\\x.gz", "Backslash"),
        ("C:/windows/x.gz", "Laufwerksbuchstaben"),
    ],
)
def test_unsichere_schluessel_werden_abgelehnt(tmp_path, bad, match):
    store = FilesystemSnapshotStore(tmp_path)
    with pytest.raises(ValueError, match=match):
        store.put(bad, b"x")


def test_zweites_put_ist_idempotent(tmp_path):
    store = FilesystemSnapshotStore(tmp_path)
    key = store.build_key("books.toscrape.com", FETCHED, HASH)
    store.put(key, b"erste fassung")
    on_disk = tmp_path.joinpath(*key.split("/"))
    stat1 = on_disk.stat()

    # Inhaltsadressiert: gleicher Schluessel -> kein Rewrite, auch bei anderem Body.
    store.put(key, b"voellig anderer inhalt")
    stat2 = on_disk.stat()
    assert stat2.st_mtime_ns == stat1.st_mtime_ns
    assert stat2.st_ino == stat1.st_ino
    assert store.get(key) == b"erste fassung"


def test_ist_ein_snapshotstore(tmp_path):
    assert isinstance(FilesystemSnapshotStore(tmp_path), SnapshotStore)

    class Halb:
        def put(self, blob_key, body): ...

    assert not isinstance(Halb(), SnapshotStore)
