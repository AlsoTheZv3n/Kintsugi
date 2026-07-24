"""``kintsugi fixtures capture``: Snapshots ausschliesslich ueber den HttpFetcher.

docs/07-test-targets.md §Stufe 0. Der Capture-Pfad nutzt denselben
``HttpFetcher`` wie die Pipeline — robots (protego, 404-heisst-allow per F1),
0.5-rps-Token-Bucket, Retry und Snapshot-vor-Parsing gelten also auch hier. Ein
zweiter HTTP-Pfad machte die Politeness-Zusage der README unwahr fuer genau den
Verkehr, der die Live-Sandbox am haertesten trifft.

Eine fest verdrahtete Allowlist schaltet das Kommando frei; ``webscraper.io`` ist
ausgeschlossen, weil ``Disallow: /test-sites/e-commerce/`` gilt (F4).

Zwei disjunkte Wurzeln (die On-Disk-Vertrag fuer das ganze Projekt):

- ``fixtures/<domain>/<entity>/golden/<label>/{page.html.gz, meta.json}``
- ``fixtures/<domain>/<entity>/corpus/<sha256>.html.gz`` plus eine
  ``corpus/manifest.json`` (Pfad -> Blob, mit ETag/Last-Modified/Content-Type).
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import ValidationError

from kintsugi.harness.fixture_model import FixtureMeta

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy import Connection

    from kintsugi.fetch.base import Fetcher
    from kintsugi.storage.snapshots import SnapshotStore

__all__ = [
    "DENY",
    "DomainNotAllowed",
    "build_index",
    "capture_corpus",
    "capture_golden",
    "guard_domain",
    "label_dirname",
    "verify_index",
    "write_index",
]


def label_dirname(label: str) -> str:
    """Dateisystem-sicherer Verzeichnisname fuer ein Label.

    Windows verbietet ``:`` in Pfaden, das echte Label ``edge:<slug>`` kann also
    nicht direkt Verzeichnis sein. Das wahre Label bleibt in ``meta.json`` unter
    ``golden_label`` erhalten; nur der Ordnername wird entschaerft.
    """
    return label.replace(":", "__")


ALLOWLIST = frozenset({"books.toscrape.com", "quotes.toscrape.com", "scrapethissite.com"})
# webscraper.io ist bewusst NICHT freigeschaltet: robots.txt verbietet den
# E-Commerce-Testbereich, den wir sonst spiegeln wuerden (F4).
DENY = {"webscraper.io": "Disallow: /test-sites/e-commerce/"}


class DomainNotAllowed(Exception):
    """Die Domain ist nicht in der Allowlist (oder ausdruecklich verboten)."""

    def __init__(self, domain: str, reason: str) -> None:
        super().__init__(f"{domain}: {reason}")
        self.domain = domain
        self.reason = reason


def guard_domain(domain: str) -> None:
    """Wirft ``DomainNotAllowed``, bevor irgendein HTTP-Verkehr entsteht."""
    if domain in DENY:
        raise DomainNotAllowed(domain, DENY[domain])
    if domain not in ALLOWLIST:
        raise DomainNotAllowed(domain, "nicht in der Capture-Allowlist")


@dataclass
class CaptureRun:
    """Sammelt geschriebene Pfade fuer die Ausgabe."""

    written: list[Path] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(UTC)


def capture_golden(
    root: Path,
    *,
    domain: str,
    entity: str,
    label: str,
    url: str,
    fetcher: Fetcher,
    synthetic: bool = False,
    derived_from: str | None = None,
    edit: str | None = None,
) -> Path:
    """Faengt eine benannte Golden-Fixture ueber den Fetcher ab."""
    guard_domain(domain)
    result = fetcher.fetch(url)
    body = result.body
    dest = root / domain / entity / "golden" / label_dirname(label)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "page.html.gz").write_bytes(gzip.compress(body, mtime=0))
    meta = FixtureMeta(
        url=url,
        fetched_at=_now(),
        http_status=result.http_status,
        content_type=result.content_type,
        content_hash=hashlib.sha256(body).hexdigest(),
        byte_size=len(body),
        fetcher=result.fetcher,
        golden_label=label,
        synthetic=synthetic,
        derived_from=derived_from,
        edit=edit,
    )
    (dest / "meta.json").write_text(meta.model_dump_json(indent=2), encoding="utf-8")
    return dest


def capture_corpus(
    root: Path,
    *,
    domain: str,
    entity: str,
    urls: list[str],
    fetcher: Fetcher,
) -> Path:
    """Faengt den Bulk-Corpus ab: ein Blob je Inhalt, ein Pfad->Blob-Manifest."""
    guard_domain(domain)
    corpus = root / domain / entity / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, object]] = {}
    for url in urls:
        result = fetcher.fetch(url)
        body = result.body
        digest = hashlib.sha256(body).hexdigest()
        blob = corpus / f"{digest}.html.gz"
        if not blob.exists():
            blob.write_bytes(gzip.compress(body, mtime=0))
        request_path = urlsplit(url).path
        manifest[request_path] = {
            "blob": blob.name,
            "http_status": result.http_status,
            "content_type": result.content_type,
            "etag": result.headers.get("etag"),
            "last_modified": result.headers.get("last-modified"),
        }
    (corpus / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )
    return corpus


# Diese Praefix-Wurzeln sind manifest-befreit (I1.3.1).
_EXEMPT_ROOTS = ("_selftest", "_synthetic")


def _is_exempt_dir(rel: Path) -> bool:
    parts = rel.parts
    return (parts and parts[0] in _EXEMPT_ROOTS) or "corpus" in parts


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_index(root: Path) -> dict[str, object]:
    """Erzeugt den regenerierbaren Fixture-Index (deterministisch sortiert).

    Je Golden-Fixture den sha256 von ``page.html.gz`` **und** ``expected.json``
    (soweit vorhanden) — die Datei-Hashes, nicht der Body-Hash aus meta.json,
    damit ein einziges editiertes Byte im Review sichtbar wird. ``_selftest/``,
    ``_synthetic/`` und jeder ``*/corpus/``-Pfad sind ausgenommen und stehen
    unter ``exempt``.
    """
    golden: dict[str, dict[str, str]] = {}
    exempt: set[str] = {root_name + "/" for root_name in _EXEMPT_ROOTS}
    if root.is_dir():
        for meta_path in sorted(root.rglob("meta.json")):
            rel = meta_path.parent.relative_to(root)
            if _is_exempt_dir(rel):
                continue
            try:
                FixtureMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
            except ValidationError:
                # Fremdes Golden-Format (z. B. der CssExtractor-Baseline mit
                # 'expected'-Block) — nicht von diesem Index verwaltet.
                continue
            entry: dict[str, str] = {}
            for filename in ("page.html.gz", "expected.json"):
                target = meta_path.parent / filename
                if target.is_file():
                    entry[filename] = _sha256_file(target)
            golden[rel.as_posix()] = entry
        for corpus_dir in sorted(root.rglob("corpus")):
            if corpus_dir.is_dir():
                exempt.add(corpus_dir.relative_to(root).as_posix() + "/")
    return {
        "version": 1,
        "exempt": sorted(exempt),
        "golden": dict(sorted(golden.items())),
    }


def verify_index(root: Path) -> list[str]:
    """Golden-Pfade, deren Datei-Hashes vom committeten ``index.json`` abweichen.

    Leere Liste = der Baum passt zum Manifest. Ein editiertes Byte in einer
    Golden-Datei taucht hier als Pfad auf.
    """
    index_path = root / "index.json"
    if not index_path.is_file():
        return ["index.json fehlt"]
    committed = json.loads(index_path.read_text(encoding="utf-8"))
    current = build_index(root)
    offenders: list[str] = []
    committed_golden = committed.get("golden", {})
    current_golden = current["golden"]
    assert isinstance(current_golden, dict)
    for path in sorted(set(committed_golden) | set(current_golden)):
        if committed_golden.get(path) != current_golden.get(path):
            offenders.append(path)
    return offenders


def write_index(root: Path) -> Path:
    """Schreibt ``<root>/index.json`` und gibt den Pfad zurueck."""
    index_path = root / "index.json"
    index_path.write_text(
        json.dumps(build_index(root), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return index_path


def import_golden(
    conn: Connection,
    *,
    site_pack_id: UUID,
    domain: str,
    entity: str,
    root: Path,
    store: SnapshotStore,
) -> int:
    """Spielt die On-Disk-Golden-Fixtures als ``is_golden``-Snapshots ein.

    Ein synthetischer ``replay``-Lauf traegt die Zeilen; jeder Blob geht durch den
    Phase-0-Snapshot-Store. Idempotent auf ``content_hash``: ein zweiter Aufruf
    importiert nichts neu. Fremde Golden-Formate (der CssExtractor-Baseline) ohne
    ``FixtureMeta`` werden uebersprungen.
    """
    from sqlalchemy import func, insert, select

    from kintsugi.storage.tables import run as run_table
    from kintsugi.storage.tables import snapshot

    golden_root = root / domain / entity / "golden"
    if not golden_root.is_dir():
        return 0

    run_id: UUID | None = None
    imported = 0
    for meta_path in sorted(golden_root.rglob("meta.json")):
        try:
            meta = FixtureMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
        except ValidationError:
            continue
        page = meta_path.parent / "page.html.gz"
        if not page.is_file():
            continue
        content_hash = bytes.fromhex(meta.content_hash)
        already = conn.execute(
            select(func.count())
            .select_from(snapshot)
            .where(snapshot.c.content_hash == content_hash, snapshot.c.is_golden.is_(True))
        ).scalar_one()
        if already:
            continue

        if run_id is None:  # Lauf nur anlegen, wenn wirklich importiert wird.
            run_id = conn.execute(
                insert(run_table)
                .values(
                    site_pack_id=site_pack_id, trigger="replay", status="ok", finished_at=func.now()
                )
                .returning(run_table.c.id)
            ).scalar_one()

        body = gzip.decompress(page.read_bytes())
        blob_key = store.build_key(domain, meta.fetched_at, content_hash)
        store.put(blob_key, body)
        conn.execute(
            insert(snapshot).values(
                run_id=run_id,
                url=meta.url,
                fetched_at=meta.fetched_at,
                http_status=meta.http_status,
                content_hash=content_hash,
                content_type=meta.content_type,
                byte_size=meta.byte_size,
                blob_key=blob_key,
                fetcher=meta.fetcher,
                is_golden=True,
                golden_label=meta.golden_label,
            )
        )
        imported += 1
    conn.commit()
    return imported
