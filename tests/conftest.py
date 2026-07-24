"""Gemeinsame Testkonfiguration.

Enthaelt den Netzwerk-Riegel. `docs/07-test-targets.md` verspricht fuer Stufe 0
„Laeuft bei jedem Commit, dauert Sekunden". Dieser Satz ist nur dann wahr, wenn
es strukturell unmoeglich ist, dass ein Stufe-0-Test eine Verbindung nach
draussen oeffnet — sonst wird die Suite unbemerkt netzgebunden und flakey.

Loopback bleibt offen, statt Sockets pauschal zu sperren: Integrationstests
muessen die Postgres-16-Instanz erreichen.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest

GUARD_MESSAGE = "network access in a non-live test"

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0", ""})

# Tests mit einem dieser Marker duerfen ins Netz bzw. auf loopback.
_EXEMPT_MARKERS = ("live", "integration")


def _is_loopback(address: Any) -> bool:
    """True, wenn die Zieladresse lokal ist.

    `address` ist bei AF_INET/AF_INET6 ein Tupel `(host, port, ...)` und bei
    AF_UNIX ein Pfad als str oder bytes. UNIX-Sockets sind per Definition lokal.
    """
    if isinstance(address, (str, bytes)):
        return True
    if isinstance(address, tuple) and address:
        host = address[0]
        if isinstance(host, bytes):
            host = host.decode("utf-8", "replace")
        if not isinstance(host, str):
            return False
        return host in _LOOPBACK_HOSTS or host.startswith("127.")
    return False


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Verhindert Verbindungen nach draussen in allen nicht befreiten Tests."""
    if any(request.node.get_closest_marker(m) for m in _EXEMPT_MARKERS):
        yield
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        if _is_loopback(address):
            return real_connect(self, address)
        raise RuntimeError(f"{GUARD_MESSAGE}: {address!r}")

    def guarded_connect_ex(self: socket.socket, address: Any) -> Any:
        if _is_loopback(address):
            return real_connect_ex(self, address)
        raise RuntimeError(f"{GUARD_MESSAGE}: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    yield


@pytest.fixture(scope="session")
def books_fixture_base_url() -> Iterator[str]:
    """Basis-URL des lokalen books-Corpus-Servers (127.0.0.1, ephemerer Port)."""
    from tests.fixture_server import FixtureServer

    with FixtureServer() as server:
        yield server.base_url
