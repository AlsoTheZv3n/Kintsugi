# Mitarbeit an Kintsugi

## Einrichtung

Zwei Befehle, mehr nicht:

```
uv sync
uv run pre-commit install
```

`uv sync` erzeugt die virtuelle Umgebung aus `uv.lock` und installiert das Projekt editierbar.
`uv run pre-commit install` haengt die Gates in den Commit-Hook.

Optional, nur fuer die Baseline-Profile aus `docs/08-roadmap.md` Phase 1:

```
uv sync --extra profiling
```

`ydata-profiling` liegt bewusst als Extra und nicht im Kern-Set — es zieht rund fuenfzig
weitere Pakete. Das Extra pinnt zusaetzlich `setuptools>=75,<81`, weil `ydata-profiling` zur
Laufzeit `pkg_resources` importiert. Das kommt seit Python 3.12 nicht mehr mit und wurde in
setuptools 81 entfernt; ohne den Pin scheitert der Import, obwohl das Paket installiert ist.

## Plattformen

Die Entwicklungsmaschine ist **Windows 11 mit PowerShell**, CI laeuft auf **Linux**. Alles, was
committet wird, muss auf beiden gruen sein. Konkret heisst das:

- Zeilenenden werden ueber `.gitattributes` erzwungen, nicht dem Zufall der Arbeitskopie
  ueberlassen. `snapshot.content_hash` ist ein byte-genauer sha256 — eine Datei, die unter
  Windows als CRLF und unter Linux als LF im Baum landet, erzeugt zwei verschiedene Hashes fuer
  denselben Inhalt.
- Pfade werden im Code ausschliesslich ueber `pathlib` gebildet, nie durch String-Verkettung
  mit `/`.
- `grep`, `rg`, `sed` und Konsorten stehen hier nur unter Git Bash zur Verfuegung, nicht in
  PowerShell.

## Gates

```
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

`uv run pytest` laeuft standardmaessig offline: `addopts` enthaelt `-m 'not live'`, und
`tests/conftest.py` sperrt in allen nicht befreiten Tests jede Verbindung, die nicht auf
loopback geht. Tests gegen echte Quellen tragen `@pytest.mark.live` und laufen nur mit
`uv run pytest -m live`.

Hooks werden mit `pre-commit autoupdate` aktualisiert, und zwar in einem **eigenen Commit** —
nicht beilaeufig in einer fachlichen Aenderung.

## Regel fuer Akzeptanzkriterien

Diese Regel ist bindend fuer jedes Issue im Repository.

Ein Akzeptanzkriterium ist entweder

1. **ein pytest-Knoten**, der auf Windows und Linux gleichermassen laeuft, oder
2. **ein Shell-Befehl, der ausdruecklich mit `# bash` gekennzeichnet ist.**

Ungekennzeichnete Befehle gelten als plattformneutral und duerfen deshalb kein `grep`, `rg`,
`sed`, `awk` oder `find` enthalten — das sind hier Git-Bash-Werkzeuge und keine
PowerShell-Werkzeuge.

Kriterien, die ein **Mutationsexperiment** beschreiben — „fuege voruebergehend X ein und
beobachte, dass es fehlschlaegt" — sind als Test auszudruecken, der die veraenderte Datei nach
`tmp_path` schreibt, das Werkzeug per `subprocess` darauf ausfuehrt und den Rueckgabewert
prueft. Niemals als manueller Schritt in einer PR-Beschreibung.

Der Grund ist derselbe wie beim Rest des Projekts: Ein Kriterium, das niemand automatisch
nachprueft, ist kein Kriterium, sondern eine Absichtserklaerung. `tests/unit/test_lint_gate.py`
und `tests/unit/test_network_guard.py` sind die Vorlage dafuer.

## Commits

Aussagesaetze im Imperativ, erste Zeile hoechstens 72 Zeichen. Der Rumpf erklaert **warum**,
nicht was — das steht im Diff. Ein Commit schliesst genau ein Issue und nennt es mit
`Closes #<nummer>`.
