# 09 – Architekturentscheidungen

Kurzformat: Kontext, Entscheidung, Konsequenzen, verworfene Alternativen.

---

## ADR-001 – Site-Packs sind Daten, nicht Code

**Kontext.** Das System soll seine eigene Extraktionslogik anpassen können, wenn sich eine Quelle ändert. Kommerzielle Anbieter lassen dafür ein Modell den generierten Python-Code umschreiben.

**Entscheidung.** Die Änderungsoberfläche für automatische Reparaturen ist ein schema-validiertes Konfigurationsdokument in Postgres, kein Quellcode. Python-Hooks existieren als Escape Hatch, sind aber von der Heilung ausgeschlossen.

**Konsequenzen.** Der Explosionsradius eines schlechten Vorschlags ist auf einen Selektorstring begrenzt. Rollback ist ein Statuswechsel statt eines Redeployments. Vorschläge sind vor der Ausführung statisch prüfbar. Modelle sind beim Ausfüllen eines Schemas zuverlässiger als beim Schreiben lauffähigen Codes. Preis: Sonderfälle, die sich nicht deklarativ ausdrücken lassen, brauchen einen Menschen – das ist akzeptiert und macht fehlende Transform-Primitiven sichtbar.

**Verworfen.** Codegenerierung mit Sandbox-Ausführung. Größere Ausdrucksstärke, aber unbegrenzter Änderungsraum, schwer beurteilbare Diffs und eine zusätzliche Sandbox als Angriffsfläche.

---

## ADR-002 – Auslöser ist Datenqualität, nicht die Ausnahme

**Kontext.** Wann gilt ein Scraper als kaputt?

**Entscheidung.** Der Auslöser ist ein Qualitätsprofil je Lauf gegen einen gleitenden Median, nicht ein geworfener Fehler.

**Konsequenzen.** Stille Degradation wird erkannt – der Fall, in dem HTTP 200 zurückkommt, der Parser durchläuft und ein Feld seit Tagen leer ist. Erfordert eine Baseline pro Feld und Quelle, die aus echten Läufen abgeleitet wird. Neue Quellen haben in den ersten Tagen keine belastbare Baseline und laufen deshalb zunächst nur beobachtend.

**Verworfen.** Exception-basierte Erkennung. Fängt genau die Klasse von Fehlern nicht, die teuer ist.

---

## ADR-003 – Postgres mit JSONB statt Dokumentendatenbank

**Kontext.** Die Nutzlast ist semi-strukturiert und je Entität verschieden.

**Entscheidung.** Postgres mit typisiertem Kern als Spalten und `JSONB` für den variablen Teil, GIN-Index darauf.

**Konsequenzen.** Provenance ist relational und referenziell abgesichert: Record verweist auf Snapshot und auf Site-Pack-Version, beides mit Fremdschlüssel. Partielle Unique-Indizes erzwingen Invarianten wie „genau eine aktive Version je Quelle" in der Datenbank statt in der Anwendung. Versionswechsel sind transaktional. Nur ein Datenbanksystem im Betrieb.

**Verworfen.** MongoDB – die Daten sind semi-strukturiert, die Beziehungen sind es nicht, und die Provenance ist der halbe Wert des Systems.

---

## ADR-004 – Heilung in aufsteigenden Kostenstufen, LLM zuletzt

**Kontext.** Ein Modell könnte jeden Bruch analysieren. Das wäre teuer, langsam und nicht reproduzierbar.

**Entscheidung.** Wertanker, dann DOM-Diff, dann LLM. Jede Stufe nur, wenn die vorige kein verifizierbares Ergebnis liefert. Das Modell erzeugt den Extraktor, es ist niemals selbst der Extraktor im Betrieb.

**Konsequenzen.** Der überwiegende Teil realer Brüche – umbenannte Klassen, rotierte Hashes, zusätzliche Wrapper – wird deterministisch und kostenlos behoben. Die LLM-Kosten skalieren mit der Zahl der Brüche, nicht mit der Zahl der Seiten. Extraktion bleibt reproduzierbar, weil im Betrieb kein Modell beteiligt ist.

**Verworfen.** LLM-Extraktion pro Seite. Bei zehntausend Seiten täglich prohibitiv teuer, langsam, und die Ergebnisse sind zwischen zwei Läufen nicht identisch – womit Drift-Erkennung unmöglich wird.

---

## ADR-005 – Kein automatischer Fix ohne Verifikationsgate

**Kontext.** Ein Vorschlag könnte direkt aktiviert werden, um die Behebungszeit zu minimieren.

**Entscheidung.** Statische Prüfung, dann Golden-Fixture-Replay, dann Canary, dann Promote, dann Nachbeobachtung. Reißt die Kette, entsteht ein Incident statt eines Deployments.

**Konsequenzen.** Behebung dauert länger als bei direkter Aktivierung. Dafür ist ein automatischer Fix belastbar und kein Vertrauensvorschuss. Der Fixture-Bestand muss gepflegt werden und Randfälle enthalten – ein Bestand aus reinen Normalfällen prüft nichts.

**Verworfen.** Direkte Aktivierung mit nachgelagerter Überwachung. Der Zeitraum zwischen Aktivierung und Erkennung schreibt falsche Daten in den Bestand, und bei SCD Typ 2 ist das nicht spurlos zu bereinigen.

---

## ADR-006 – Erreichbarkeit unabhängig vom Scrape-Lauf messen

**Kontext.** Ein fehlgeschlagener Lauf kann bedeuten, dass die Quelle ausgefallen ist oder dass der Extraktor kaputt ist. Beides verlangt gegenteiliges Handeln.

**Entscheidung.** Ein eigener Synthetic Probe je Domain, entkoppelt vom Lauf, mit eigener Metrik.

**Konsequenzen.** Die Vorprüfung der Heilung kann zwischen den beiden Fällen unterscheiden. Ohne diese Trennung würde das System gegen Cloudflare-Fehlerseiten, Wartungsseiten und Consent-Walls heilen, dabei funktionierende Selektoren zerstören und den Schaden als aktive Version festschreiben. Das ist der wahrscheinlichste Selbstzerstörungspfad des Systems, und ein separater Probe ist die billigste Absicherung dagegen.

**Verworfen.** Ableitung der Erreichbarkeit aus Statuscodes des Laufs. Genau die gefährlichen Fälle liefern Status 200.

---

## ADR-007 – SCD Typ 2 statt Überschreiben

**Kontext.** Wie werden Änderungen an einem bereits erfassten Datensatz behandelt?

**Entscheidung.** Historisierung mit `valid_from` und `valid_to`, aktuelle Zeile durch `valid_to IS NULL` gekennzeichnet, erzwungen über einen partiellen Unique-Index.

**Konsequenzen.** Vollständige Änderungshistorie je Entität. Der Change-Feed für Konsumenten fällt ohne zusätzlichen Mechanismus ab. Nach einer schlechten Heilung lassen sich exakt die betroffenen Zeilen identifizieren und zurückrollen. Zeitreisen-Abfragen für Debugging sind möglich. Preis ist Speicherplatz – bei textuellen Nutzlasten vernachlässigbar – und etwas mehr Sorgfalt beim Schreibpfad.

**Verworfen.** Upsert mit Überschreiben. Spart Speicher, macht aber Rollback nach fehlerhafter Heilung unmöglich und verlangt einen separaten Change-Feed-Mechanismus.
