# 04 – Self-Healing

## Auslöser

Nicht der Absturz. Ein abgestürzter Scraper ist ein triviales Problem: Er ist laut, sofort sichtbar und liefert einen Stacktrace. Der teure Fehler liefert HTTP 200, läuft sauber durch und schreibt seit vier Tagen `null` in ein Feld, das ein nachgelagerter Dienst für bare Münze nimmt.

Der Auslöser ist deshalb das Qualitätsprofil eines Laufs, verglichen mit dem gleitenden Median der letzten vierzehn Tage:

| Signal | Schwelle | Bedeutung |
|---|---|---|
| Fill-Rate eines Felds | unter `min_fill_rate` | Selektor trifft nicht mehr |
| Zeilenzahl | Abweichung über `row_count_deviation` | Discovery oder Paginierung gebrochen |
| Bereichsverletzungen | über 5 % der Zeilen | Selektor trifft das falsche Element |
| Enum-Verletzungen | über 0 | Format- oder Semantikänderung |
| Duplikatrate | über `max_duplicate_rate` | Natural Key gebrochen |
| Natural Key nicht extrahierbar | über 0 | immer Eskalation, nie Heilung |

Der letzte Fall ist die wichtigste Ausnahme. Ein falscher Natural Key korrumpiert den Bestand rückwirkend, weil er die Historisierung an falsche Entitäten hängt. Das ist irreversibel, sobald es unbemerkt durchläuft.

## Vorprüfung: darf überhaupt geheilt werden

Vor jedem Reparaturversuch werden Ausschlussgründe geprüft. Fällt einer davon, wird nicht geheilt, sondern der passende Incident geöffnet:

- **Quelle nicht erreichbar** – der unabhängige Synthetic Probe meldet die Domain als down. Nicht heilen, warten.
- **Blockiert** – CAPTCHA-Seite, Consent-Wall, Bot-Erkennung. Erkennbar an Signaturen im Snapshot, nicht am Statuscode. Nicht heilen, sondern Fetch-Problem melden.
- **Rate-limitiert** – gehäuft 429 oder 403. Nicht heilen, Backoff erhöhen.
- **Soft-404** – Status 200 mit Fehlerinhalt. Nicht heilen, Discovery prüfen.
- **Kontingent erschöpft** – `max_auto_versions_per_window` überschritten. An den Menschen übergeben.

Diese Vorprüfung ist kein Randfall, sondern der Hauptschutz des Systems. Ohne sie lernt der Heiler Selektoren aus einer Cookie-Banner-Seite und schreibt sie als aktive Version fest.

## Reparaturstufen

Aufsteigend nach Kosten. Jede Stufe wird nur betreten, wenn die vorige kein verifizierbares Ergebnis liefert.

### Stufe 1 – Wertanker

Deterministisch, kostenlos, deckt den häufigsten Bruch ab.

Aus dem letzten erfolgreichen Lauf sind für dieselbe URL die korrekten Werte bekannt. Der Wert `£51.77` wird im neuen DOM gesucht. Wird er in genau einem Textknoten gefunden, wird daraus ein neuer Selektor abgeleitet – bevorzugt über stabile Merkmale (`data-*`-Attribute, `itemprop`, semantische Tags, Textanker eines Nachbarelements), nicht über `nth-child` oder generierte Klassennamen.

Deckt ab: umbenannte Klassen, rotierte Hash-Klassen, zusätzliche Wrapper-Divs, getauschte Tags, verschobene Geschwister. In der Praxis ist das die deutliche Mehrheit aller Brüche.

Ist der Wert an mehreren Stellen zu finden, entscheidet die Nähe zur alten DOM-Position. Ist er gar nicht zu finden, geht es weiter zu Stufe 2.

### Stufe 2 – DOM-Diff

Der aktuelle Snapshot wird strukturell gegen den letzten Good-Snapshot derselben URL verglichen. Ergebnis ist der geänderte Teilbaum. Damit lässt sich unterscheiden:

- **Lokale Änderung** – ein Teilbaum betroffen, Rest identisch. Kandidat für Stufe 3, mit dem Teilbaum als Kontext.
- **Vollständiger Umbau** – nahezu alles anders. Meist ein Redesign; Reparatur eines einzelnen Selektors ist sinnlos, Eskalation als `schema_change`.
- **Leerer Body bei vorher gefülltem** – Umstellung auf Client-Rendering. Keine Selektorreparatur, sondern ein Wechsel der Fetch-Strategie auf `browser` – oder besser der XHR-Endpunkt, den die Seite jetzt selbst aufruft.

Der Diff verkleinert den Kontext für Stufe 3 von einem 300-KB-Dokument auf wenige Kilobyte. Das ist der Unterschied zwischen einem brauchbaren und einem geratenen Vorschlag.

### Stufe 3 – LLM-Vorschlag

Erst jetzt, mit engem Kontext und klarem Auftrag. Das Modell bekommt: den geänderten Teilbaum, den Feldnamen, den `anchor_hint`, den alten Selektor, drei bekannte korrekte Beispielwerte und das Zielschema des Felds. Es liefert ausschließlich einen Site-Pack-Patch als JSON gegen ein festes Schema – keinen Code, keine Prosa.

Der entscheidende Punkt: **Das Modell schreibt den Extraktor, es ist nicht der Extraktor.** Ein LLM pro Seite laufen zu lassen wäre teuer, langsam und nicht reproduzierbar. Der Vorschlag wird einmal erzeugt und läuft danach deterministisch.

Es werden bis zu drei Kandidaten erzeugt und alle durch das Gate geschickt. Der erste, der besteht, gewinnt.

## Freigabe-Gate

Kein Vorschlag geht ohne diese Kette live.

**Statische Prüfung** – Schema-Konformität, Selektor parsebar, Transform-Kette typverträglich, keine Änderung an `natural_key`. Abgelehnte Vorschläge kosten hier nichts.

**Golden-Fixture-Replay** – der Vorschlag läuft gegen 20 bis 50 gespeicherte Snapshots mit hinterlegtem Erwartungswert. Bestehen heißt: alle Pflichtfelder exakt korrekt, optionale Felder mindestens auf altem Niveau. Ein einziger abweichender Wert ist ein Durchfall.

Der Fixture-Bestand muss Randfälle enthalten, nicht nur den Normalfall: ausverkaufte Artikel, fehlende optionale Felder, Sonderzeichen im Titel, mehrsprachige Varianten, sehr lange und sehr kurze Werte. Ein Fixture-Set aus fünfzig identisch aufgebauten Normalfällen prüft nichts.

**Canary** – die neue Version läuft auf `canary_fraction` der URLs, parallel zur alten. Verglichen werden Fill-Rate pro Feld, Wertverteilung und Zeilenzahl. Signifikante Abweichung bedeutet Ablehnung.

**Promote** – in einer Transaktion: alte Version auf `retired`, neue auf `active`. Der partielle Unique-Index macht einen inkonsistenten Zwischenzustand unmöglich.

**Nachbeobachtung** – die nächsten drei Läufe werden verschärft überwacht. Kippen die Metriken, automatischer Rollback auf die Vorversion und Eskalation.

Ist die Kette an irgendeiner Stelle gerissen, entsteht ein Incident mit dem abgelehnten Vorschlag und dem Ablehnungsgrund als Evidence. Der Mensch fängt damit nicht bei null an, sondern korrigiert einen fast fertigen Vorschlag.

## Negativtests

Ein Heiler, der Fehlalarme repariert, ist schlechter als gar keiner: Er ersetzt einen funktionierenden Selektor durch einen aus Müll gelernten und schreibt das Ergebnis als aktive Version fest.

Diese Fälle sind Pflichttests und müssen `no_action` ergeben:

| Fall | Erwartetes Verhalten |
|---|---|
| Cookie-Banner mit Status 200 statt Inhalt | `blocked`, keine Heilung |
| Soft-404 mit Status 200 | `unreachable`, keine Heilung |
| A/B-Test, Inhalt identisch, Reihenfolge anders | keine Reaktion |
| 429 vom Rate Limiter | `rate_limited`, Backoff, keine Heilung |
| Teilausfall, 30 % der Seiten leer | Lauf `degraded`, keine Heilung |
| Quelle liefert legitim weniger Daten (Saison, Sortimentsabbau) | `row_count_anomaly` als `info`, keine Heilung |

Die Zielmetrik ist zweidimensional und wird bei jedem CI-Lauf ausgewiesen: **Heilungsrate** über die echten Bruchfälle und **Fehlalarmrate** über die Negativfälle. Eine Heilungsrate von 90 % bei 20 % Fehlalarm ist ein schlechteres System als 70 % bei 0 %.

## Was niemals automatisch geheilt wird

- Änderungen am `natural_key`
- Feld vollständig verschwunden – das ist eine fachliche Änderung der Quelle und verlangt eine Schema-Entscheidung, keine Selektorreparatur
- Neue Pflichtfelder
- Enum-Erweiterungen
- Alles, während die Quelle als `blocked` oder `unreachable` gilt
- Alles, wenn das Wochenkontingent erschöpft ist

Diese Grenze ist bewusst konservativ. Der Zweck des Systems ist nicht, ohne Menschen auszukommen, sondern die Zahl der Fälle zu minimieren, in denen ein Mensch nachts geweckt werden muss – und für die verbleibenden Fälle die Zeit bis zur Behebung zu drücken.
