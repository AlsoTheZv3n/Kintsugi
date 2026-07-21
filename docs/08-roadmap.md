# 08 – Bauplan

Sechs Phasen, jede mit einer überprüfbaren Definition of Done. Die Reihenfolge ist nicht beliebig: Der Testharness kommt vor dem Self-Healing, weil sich Heilung sonst nicht entwickeln, sondern nur raten lässt.

Zeitangaben gehen von Teilzeit neben laufenden Bewerbungen aus. Nach Phase 3 ist das System vorzeigbar; alles danach ist Ausbau.

---

## Phase 0 – Skelett (ca. 1 Woche)

Ein Durchstich ohne jede Cleverness. Ziel ist, dass Daten von einer echten Seite bis in die Datenbank fließen.

- Projektgerüst mit `uv`, `ruff`, `mypy`, `pytest`, `pre-commit`
- `docker-compose` mit Postgres, Migrationen über Alembic
- Site-Pack-Schema als Pydantic-Modell plus YAML-Loader
- ein Site-Pack für `books.toscrape.com`
- `HttpFetcher` mit Rate Limiting und Robots-Prüfung
- Snapshot-Persistenz, zunächst Dateisystem statt Objektspeicher
- `CssExtractor` und die Transform-Primitiven `strip`, `parse_currency`, `int_from_text`
- synchroner Runner, kein Scheduler
- CLI: `kintsugi run <domain>`

**Definition of Done:** `uv run kintsugi run books.toscrape.com` schreibt mindestens 200 validierte Records nach Postgres, jeder mit Verweis auf Snapshot und Site-Pack-Version. Zweiter Lauf schreibt keine Duplikate.

---

## Phase 1 – Verträge und Harness (ca. 1 Woche)

Die Phase, die den Rest möglich macht. Hier entsteht das Fundament, gegen das jede spätere automatische Änderung geprüft wird.

- Qualitätsprofil pro Lauf: Fill-Rate je Feld, Zeilenzahl gegen Median, Bereichs- und Enum-Verletzungen, Duplikatrate
- Pandera-Schemata aus der Site-Pack-Deklaration generieren
- Golden-Fixture-Bestand: 30 Snapshots je Quelle inklusive Randfällen, mit Erwartungswerten
- Replay-Harness: Site-Pack gegen Fixtures ausführen, feldweiser Vergleich
- Mutations-Harness mit dem Katalog aus `07-test-targets.md`, alle 22 Mutationen
- zweite und dritte Quelle: `quotes.toscrape.com/js` und `webscraper.io/test-sites/e-commerce/ajax`
- Baseline-Profil je Quelle mit `ydata-profiling`, daraus die Schwellwerte ableiten statt raten

**Definition of Done:** `pytest` fährt das Kreuzprodukt aus drei Quellen und zweiundzwanzig Mutationen. Ohne Heilung ist das erwartete Ergebnis überall `escalated` beziehungsweise `no_action` – der Harness klassifiziert korrekt, obwohl noch nichts repariert wird. Die Bewertungstabelle wird ausgegeben.

---

## Phase 2 – Heilung, deterministisch (ca. 1 Woche)

Noch ohne LLM. Das ist Absicht: Der deterministische Teil deckt den Großteil ab, kostet nichts und lässt sich exakt bewerten.

- Versionierung der Site-Packs mit Statusmaschine und partiellen Unique-Indizes
- Vorprüfung: erreichbar, blockiert, rate-limitiert, Soft-404, Kontingent
- unabhängiger Synthetic Probe pro Domain
- Wertanker-Reparatur inklusive Selektorableitung nach Stabilitätsheuristik
- statische Vorprüfung der Vorschläge
- Golden-Fixture-Gate
- Canary-Ausführung mit statistischem Vergleich
- Promote in einer Transaktion, Rollback bei Kippen der Nachbeobachtung
- Incident-Erzeugung mit vollständiger Evidence

**Definition of Done:** M01 bis M05 werden automatisch geheilt, N01 bis N06 lösen keine Heilung aus, M13 bis M16 eskalieren. Fehlalarmrate null. Ein manuell zerstörter Selektor auf `books.toscrape.com` wird im nächsten Lauf ohne Zutun repariert und die neue Version ist in der Datenbank sichtbar.

---

## Phase 3 – Auslieferung und Sichtbarkeit (ca. 1 Woche)

Ab hier ist das System vorzeigbar.

- FastAPI mit Liste, Einzelabruf, Historie, Change-Feed, Quellenstatus
- Cursor-Paginierung, ETag, Feldprojektion, API-Keys mit Scopes
- Prometheus-Metriken aus `06-observability.md`
- Grafana mit Flottenübersicht, Quellendetail und Heilungsbilanz
- Alertmanager mit den drei Stufen, Zustellung über ntfy oder Telegram
- Container nach den Hausregeln, `docker-compose` für den Gesamtstack

**Definition of Done:** Ein separater kleiner Dienst konsumiert die API über den Change-Feed und verarbeitet Änderungen weiter. Ein absichtlich herbeigeführter Bruch löst binnen eines Laufintervalls eine Benachrichtigung auf dem Telefon aus, und das Grafana-Dashboard zeigt den Einbruch der Fill-Rate mit dem Versionswechsel in derselben Zeitachse.

---

## Phase 4 – LLM und Workbench (ca. 2 Wochen)

Der Teil, der im Vorstellungsgespräch gezeigt wird.

- DOM-Diff mit Teilbaum-Eingrenzung
- LLM-Vorschlagsstufe, Ausgabe strikt als schema-validierter Patch, bis zu drei Kandidaten
- provider-agnostischer Adapter, wie schon in medrag und H2H
- Kostenerfassung pro Vorschlag
- Incident-Workbench als Next.js-Frontend: Beweislage, DOM-Diff, Selektor-Editor mit Sofortauswertung, Ein-Klick-Fixture-Replay, Aktionen inklusive „als Fehlalarm schließen"
- Rückkopplung: geschlossene Fehlalarme werden automatisch Negativfälle im Katalog

**Definition of Done:** Ein Demoskript, das in unter neunzig Sekunden läuft – Selektor zerstören, Lauf starten, Alarm empfangen, Workbench öffnen, KI-Vorschlag prüfen, per Klick übernehmen, grüner Lauf. Das ist die Demonstration, nicht das Architekturdiagramm.

---

## Phase 5 – Ausbau (offen)

- Dagster als Orchestrator, Site-Packs als Assets mit Freshness Policies
- Playwright-Fetcher mit Ressourcenblockierung
- SeaweedFS als Objektspeicher, Parquet-Export für DuckDB
- Proxy-Adapter für die Fälle, in denen es ohne nicht geht
- weitere Quellen bis etwa zehn Domains
- Stufe-3-Ziele mit API-Gegenprobe im Nachtlauf

---

## Reihenfolge-Begründung

**Warum der Harness vor der Heilung kommt.** Ohne reproduzierbare Bruchszenarien wird Self-Healing zu einer Folge von Vermutungen, die sich nicht bewerten lassen. Mit dem Harness ist jede Änderung am Heiler sofort messbar an drei Zahlen.

**Warum Wertanker vor dem LLM kommt.** Er deckt den größten Teil der realen Brüche ab, ist deterministisch und kostenlos. Wer mit dem LLM anfängt, baut ein teures System, das die einfachen Fälle unnötig kompliziert löst – und merkt nicht, dass die schwierigen ohnehin eskaliert werden müssen.

**Warum die Workbench zuletzt kommt.** Sie ist das aufwendigste Stück Frontend und wertlos, solange es keine echten Incidents mit echter Evidence gibt. Zuerst muss das System Vorfälle produzieren, die es zu bearbeiten lohnt.

## Was das Projekt belegt

Für Stellen als AI Engineer oder Automation Engineer sind die belegten Fähigkeiten der eigentliche Ertrag: ein Plugin-System mit Strategy und Adapter über mehrere Erweiterungspunkte, deklarative Konfiguration als Änderungsoberfläche für ein Modell statt Codegenerierung, LLM-Einsatz mit Verifikationsgate statt blindem Vertrauen, SCD-Typ-2-Datenmodellierung mit vollständiger Provenance, Observability mit sinnvoll gestuftem Alarmdesign, und eine Teststrategie, die Fehlalarme genauso ernst nimmt wie Treffer.

Der letzte Punkt ist der, der im Gespräch am stärksten wirkt. Fast jeder kann erzählen, dass seine KI etwas repariert. Zu zeigen, dass man die Fälle systematisch abgesichert hat, in denen sie es gerade nicht tun darf, ist selten.
