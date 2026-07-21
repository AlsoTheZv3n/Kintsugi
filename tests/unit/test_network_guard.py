"""Belegt, dass der Netzwerk-Riegel aus tests/conftest.py wirkt.

Ein Riegel, der nie ausgeloest hat, ist von einem fehlenden Riegel nicht zu
unterscheiden. Die Tests hier bauen deshalb in `tmp_path` eine eigene kleine
Suite auf, die dieselbe conftest.py laedt, und fuehren pytest per Subprozess
darauf aus. Kein Test hier oeffnet selbst eine Verbindung nach draussen.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFTEST = PROJECT_ROOT / "tests" / "conftest.py"

PYTEST_INI = """\
[pytest]
markers =
    unit: reine Einheitentests
    integration: benoetigt loopback
    mutation: Mutations-Harness
    live: greift auf echte externe Quellen zu
    slow: laeuft laenger als eine Sekunde
"""


@pytest.fixture
def sandbox(tmp_path):
    """Minimale Suite in tmp_path, die dieselbe conftest.py benutzt."""
    (tmp_path / "conftest.py").write_text(CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "pytest.ini").write_text(PYTEST_INI, encoding="utf-8")
    return tmp_path


def _run_pytest(cwd, *args):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def test_unmarkierter_test_kommt_nicht_ins_netz(sandbox):
    """Ein Test ohne Marker, der nach draussen verbindet, muss scheitern."""
    (sandbox / "test_outbound.py").write_text(
        "import socket\ndef test_outbound():\n    socket.socket().connect(('93.184.216.34', 80))\n",
        encoding="utf-8",
    )
    result = _run_pytest(sandbox, "test_outbound.py")
    output = result.stdout + result.stderr
    assert result.returncode != 0, f"Der Riegel hat nicht ausgeloest:\n{output}"
    assert "network access in a non-live test" in output, output


def test_live_marker_hebt_den_riegel_auf(sandbox):
    """Mit `live` ist socket.connect wieder die unveraenderte Originalmethode.

    Geprueft wird die Aufhebung selbst, nicht eine echte Verbindung — ein Test,
    der dafuer ins Netz greift, waere genau das, was der Riegel verhindern soll.
    """
    (sandbox / "test_live.py").write_text(
        "import socket, _socket, pytest\n"
        "@pytest.mark.live\n"
        "def test_live():\n"
        "    # Ungepatcht erbt socket.socket.connect direkt von _socket.socket.\n"
        "    assert socket.socket.connect is _socket.socket.connect\n"
        "    assert 'guarded' not in getattr(socket.socket.connect, '__name__', '')\n",
        encoding="utf-8",
    )
    deselected = _run_pytest(sandbox, "test_live.py", "-m", "not live")
    assert "1 deselected" in deselected.stdout, deselected.stdout

    selected = _run_pytest(sandbox, "test_live.py", "-m", "live")
    assert selected.returncode == 0, selected.stdout + selected.stderr
    assert "1 passed" in selected.stdout, selected.stdout


def test_integration_marker_erlaubt_loopback(sandbox):
    """Mit `integration` muss eine Verbindung auf loopback durchgehen."""
    (sandbox / "test_loopback.py").write_text(
        "import socket, pytest\n"
        "@pytest.mark.integration\n"
        "def test_loopback():\n"
        "    server = socket.socket()\n"
        "    server.bind(('127.0.0.1', 0))\n"
        "    server.listen(1)\n"
        "    port = server.getsockname()[1]\n"
        "    client = socket.socket()\n"
        "    client.connect(('127.0.0.1', port))\n"
        "    client.close()\n"
        "    server.close()\n",
        encoding="utf-8",
    )
    result = _run_pytest(sandbox, "test_loopback.py", "-m", "integration")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout, result.stdout


def test_loopback_bleibt_auch_ohne_marker_offen(sandbox):
    """Der Riegel sperrt nach draussen, nicht pauschal jeden Socket."""
    (sandbox / "test_local.py").write_text(
        "import socket\n"
        "def test_local():\n"
        "    server = socket.socket()\n"
        "    server.bind(('127.0.0.1', 0))\n"
        "    server.listen(1)\n"
        "    client = socket.socket()\n"
        "    client.connect(('127.0.0.1', server.getsockname()[1]))\n"
        "    client.close()\n"
        "    server.close()\n",
        encoding="utf-8",
    )
    result = _run_pytest(sandbox, "test_local.py")
    assert result.returncode == 0, result.stdout + result.stderr


def test_strict_markers_lehnt_unbekannten_marker_ab(sandbox):
    """--strict-markers muss einen nicht deklarierten Marker zum Fehler machen."""
    (sandbox / "test_bogus.py").write_text(
        "import pytest\n@pytest.mark.nichtdeklariert\ndef test_x():\n    pass\n",
        encoding="utf-8",
    )
    result = _run_pytest(sandbox, "test_bogus.py", "--strict-markers")
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "not found in `markers` configuration option" in output, output


@pytest.mark.integration
def test_loopback_im_echten_integrationslauf():
    """Gegenprobe im echten Suite-Kontext, nicht im Sandkasten."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket()
    client.connect(("127.0.0.1", server.getsockname()[1]))
    client.close()
    server.close()
