"""Lokaler Fixture-Server fuer den offline books-Corpus (I0.9.9).

docs/07 warnt: 0.5 rps ueber ~1050 URLs sind ~35 Minuten und die Sandbox-Seiten
verschwinden. Die DoD laeuft deshalb gegen diesen Server. Er

- bindet ``127.0.0.1`` auf einem ephemeren Port (0),
- spielt die aufgenommenen ``ETag``/``Last-Modified`` zurueck und beantwortet
  ``If-None-Match`` mit einem koerperlosen ``304``,
- sendet ``Content-Type: text/html`` **ohne** charset-Parameter, genau wie die
  echte Seite — ein erfundenes ``charset=utf-8`` machte den Charset-Test hohl,
- liefert **404** fuer ``/robots.txt``, ``/sitemap.xml`` und jeden Pfad ausserhalb
  des Manifests (u. a. ``/catalogue/page-13.html``) und uebt so die
  robots-404-allow-all-Regel und den Paginierungs-Terminator offline (F1).
"""

from __future__ import annotations

import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "books.toscrape.com" / "book" / "corpus"

# 404 auch fuer diese Pfade — F1 (robots/sitemap fehlen live) explizit geuebt.
_ALWAYS_404 = frozenset({"/robots.txt", "/sitemap.xml"})


def _load_manifest() -> dict[str, dict[str, Any]]:
    return json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))


class _Handler(BaseHTTPRequestHandler):
    manifest: ClassVar[dict[str, dict[str, Any]]] = {}

    def log_message(self, *args: object) -> None:
        """Kein stderr-Rauschen in der Testausgabe."""

    def _entry(self) -> dict[str, Any] | None:
        path = self.path.split("?", 1)[0]
        if path in _ALWAYS_404:
            return None
        return self.manifest.get(path)

    def do_GET(self) -> None:
        entry = self._entry()
        if entry is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        etag = entry.get("etag")
        if etag and self.headers.get("If-None-Match") == etag:
            # Bedingte Anfrage, unveraendert: koerperlos, aber Validatoren zurueck.
            self.send_response(304)
            self.send_header("ETag", etag)
            if entry.get("last_modified"):
                self.send_header("Last-Modified", entry["last_modified"])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        body = gzip.decompress((CORPUS / entry["blob"]).read_bytes())
        self.send_response(entry.get("http_status", 200))
        # Content-Type woertlich, ohne charset — wie die echte Seite.
        self.send_header("Content-Type", entry.get("content_type", "text/html"))
        if etag:
            self.send_header("ETag", etag)
        if entry.get("last_modified"):
            self.send_header("Last-Modified", entry["last_modified"])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FixtureServer:
    """Startet den Corpus-Server in einem Thread und liefert die Basis-URL."""

    def __init__(self) -> None:
        handler = type("BooksHandler", (_Handler,), {"manifest": _load_manifest()})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        host, port = self._server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> FixtureServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
