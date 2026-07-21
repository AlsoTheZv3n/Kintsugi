"""Sichert ab, dass Zeilenenden den sha256 gespeicherter Bytes nicht bewegen.

`docs/03-data-model.md`, Abschnitt „Bronze", definiert
``content_hash bytea NOT NULL -- sha256 des Rohkoerpers``. Dieser Hash ist
zugleich der Deduplizierungsschluessel, und die Golden Fixtures sind die
Grundlage des Freigabe-Gates aus `docs/04-self-healing.md`.

Die Entwicklungsmaschine laeuft mit ``core.autocrlf=true``, CI auf Linux. Ohne
`.gitattributes` schriebe Git beim Auschecken die Zeilenenden um und derselbe
Inhalt haette auf beiden Plattformen zwei verschiedene Hashes — der Bestand
waere still unbrauchbar.

Der Erwartungswert ist bewusst als Literal hinterlegt. Wuerde der Test ihn aus
der geprueften Datei neu berechnen, waere er immer gruen und wertlos.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = PROJECT_ROOT / "fixtures" / "_selftest" / "bytes.bin"

# Nach `git add --renormalize .` ermittelt. Nicht anpassen, ohne zu verstehen,
# warum sich der Inhalt geaendert hat.
EXPECTED_SHA256 = "2c2bd78b910c843d9ae1eeb573098d01e928af3251d03550bd0e4fe1dea07dd7"
EXPECTED_BYTES = 71


def test_fixture_hat_den_erwarteten_digest():
    """Der sha256 muss unter Windows und unter Linux derselbe sein."""
    payload = FIXTURE.read_bytes()
    assert len(payload) == EXPECTED_BYTES
    assert hashlib.sha256(payload).hexdigest() == EXPECTED_SHA256


def test_fixture_enthaelt_die_kritischen_bytefolgen():
    """CRLF, einzelnes LF, Umlaute und das Pfundzeichen muessen drin sein.

    Das Pfundzeichen ist nicht dekorativ: `price` extrahiert ``£51.77`` und
    `currency` wird aus genau diesem Symbol abgeleitet (Live-Befund F3).
    """
    payload = FIXTURE.read_bytes()
    assert b"\r\n" in payload, "kein CRLF-Paar — der Test prueft dann nichts"
    assert b"LF:\n" in payload, "kein einzelnes LF"
    assert "ä".encode() in payload
    assert "£".encode() in payload
    assert not payload.endswith(b"\n"), "abschliessende Zeilenschaltung wuerde den Test entwerten"


def test_git_behandelt_die_fixture_als_binaer():
    """`fixtures/** -text -diff` muss greifen, sonst normalisiert Git sie doch."""
    result = subprocess.run(
        ["git", "check-attr", "text", "diff", "--", "fixtures/_selftest/bytes.bin"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("git nicht verfuegbar")
    assert "text: unset" in result.stdout, result.stdout
    assert "diff: unset" in result.stdout, result.stdout


def test_arbeitskopie_und_index_sind_byte_gleich():
    """Was Git ausliefert, muss dem entsprechen, was der Test hasht."""
    result = subprocess.run(
        ["git", "cat-file", "-p", ":fixtures/_selftest/bytes.bin"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("Datei noch nicht im Index")
    assert hashlib.sha256(result.stdout).hexdigest() == EXPECTED_SHA256, (
        "Der Blob im Index unterscheidet sich von der Arbeitskopie — "
        "Git normalisiert die Fixture trotz .gitattributes"
    )


def test_gitattributes_erzwingt_lf_fuer_textdateien():
    """Ohne `* text=auto eol=lf` ist die Plattformunabhaengigkeit Zufall."""
    text = (PROJECT_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "* text=auto eol=lf" in text
    assert "fixtures/** -text -diff" in text
