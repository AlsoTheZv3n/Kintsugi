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

from kintsugi.harness.fixture_model import FixtureMeta

if TYPE_CHECKING:
    from kintsugi.fetch.base import Fetcher

__all__ = [
    "DENY",
    "DomainNotAllowed",
    "build_index",
    "capture_corpus",
    "capture_golden",
    "guard_domain",
    "label_dirname",
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


def _is_exempt_dir(rel: Path) -> bool:
    parts = rel.parts
    return parts[0] == "_selftest" or "corpus" in parts


def build_index(root: Path) -> dict[str, object]:
    """Erzeugt den regenerierbaren Fixture-Index (deterministisch sortiert).

    Golden-Fixtures werden ueber ``content_hash`` aus ihrer ``meta.json``
    verankert; ``_selftest/`` und jeder ``*/corpus/``-Pfad sind ausgenommen und
    stehen unter ``exempt``.
    """
    golden: dict[str, dict[str, object]] = {}
    exempt: set[str] = {"_selftest/"}
    if root.is_dir():
        for meta_path in sorted(root.rglob("meta.json")):
            rel = meta_path.parent.relative_to(root)
            if _is_exempt_dir(rel):
                continue
            meta = FixtureMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
            golden[rel.as_posix()] = {
                "content_hash": meta.content_hash,
                "byte_size": meta.byte_size,
            }
        for corpus_dir in sorted(root.rglob("corpus")):
            if corpus_dir.is_dir():
                exempt.add(corpus_dir.relative_to(root).as_posix() + "/")
    return {
        "version": 1,
        "exempt": sorted(exempt),
        "golden": dict(sorted(golden.items())),
    }


def write_index(root: Path) -> Path:
    """Schreibt ``<root>/index.json`` und gibt den Pfad zurueck."""
    index_path = root / "index.json"
    index_path.write_text(
        json.dumps(build_index(root), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return index_path
