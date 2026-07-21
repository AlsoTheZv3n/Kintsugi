# 07 – Testziele und Mutations-Harness

## Der Denkfehler, den es zu vermeiden gilt

Der naheliegende Plan lautet: Seiten suchen, die sich oft ändern, daran das Self-Healing testen. Das funktioniert nicht. Eine „volatile" Seite bricht vielleicht alle drei Wochen, dann in genau einer Variante, unangekündigt, ohne Kontrollgruppe und nicht wiederholbar. Damit lässt sich weder entwickeln noch regressionstesten, und ein CI-Lauf kann nicht darauf warten.

Die Umkehrung löst das Problem: **echte Snapshots nehmen und selbst kontrolliert kaputt machen.** Siebzehn Bruchszenarien in zwei Sekunden statt eines in drei Monaten, deterministisch, mit bekanntem Sollverhalten.

Echte Seiten bleiben trotzdem nötig – aber für eine andere Frage. Sie beantworten nicht „heilt mein System", sondern „trifft mein Mutationskatalog die Realität" und „hält meine Fetch-Schicht der echten Welt stand". JavaScript, Anti-Bot, Rate Limits, Consent-Walls und Latenzverhalten lassen sich nicht sinnvoll simulieren.

## Stufen

### Stufe 0 – Fixtures und Mutationen (CI)

Der Regressionsbestand. Zwanzig bis fünfzig echte Snapshots pro Quelle mit hinterlegtem Erwartungswert, darauf der Mutations-Harness. Läuft bei jedem Commit, dauert Sekunden, ist die Grundlage des Golden-Fixture-Gates.

Der Fixture-Bestand muss Randfälle enthalten: ausverkaufte Artikel, fehlende optionale Felder, Sonderzeichen und Umlaute, sehr lange und sehr kurze Werte, mehrsprachige Varianten, Seiten mit null Treffern. Fünfzig identisch aufgebaute Normalfälle prüfen nichts.

### Stufe 1 – Eigene Kanarienvogel-Seite

Eine selbst deployte Seite (GitHub Pages oder ein kleiner Container), deren Template ein nächtlicher Cronjob nach einem festen Katalog zufällig mutiert und die durchgeführte Mutation in einer Manifestdatei protokolliert.

Der Zweck ist nicht, den Extraktor zu testen – das macht Stufe 0 gründlicher. Der Zweck ist, die **gesamte Betriebskette** zu üben: Zeitplanung, Erkennung, Alarmzustellung aufs Telefon, Heilung, Canary, Promote, Dashboard-Aktualisierung. Und mit dem Manifest existiert eine Wahrheit, gegen die sich das Systemverhalten automatisch bewerten lässt. Aufwand rund ein halber Tag, Nutzen dauerhaft.

### Stufe 2 – Sandboxes

Explizit zum Üben gebaut, Scraping ausdrücklich vorgesehen. Vor Verwendung Erreichbarkeit prüfen – solche Seiten verschwinden gelegentlich.

| Ziel | Deckt ab |
|---|---|
| `books.toscrape.com` | statisches HTML, Paginierung, Detailseiten. Basisfall |
| `quotes.toscrape.com` | mehrere Varianten unter einer Domain: `/js` clientseitig gerendert, `/js-delayed` verzögert, `/scroll` Infinite Scroll, `/tableful` absichtlich furchtbares Markup, `/login` Sitzungen, `/search.aspx` ViewState. Der wertvollste Sandbox überhaupt, weil dieselben Daten in sechs Ausprägungen vorliegen |
| `scrapethissite.com/pages/` | Formulare, Paginierung, AJAX, Frames, Login mit Cookies |
| `webscraper.io/test-sites/e-commerce/` | E-Commerce in Varianten `static`, `ajax`, `more`, `scroll` |
| `scrapeme.live/shop/` | WooCommerce-Struktur, realistische Produktseiten |
| `httpbin.org` | Statuscodes, Verzögerungen, Weiterleitungen, Header, Kompression. Für Retry-, Backoff- und Timeout-Logik unersetzlich |

Die Varianten von `quotes.toscrape.com` sind der Grund, warum die Extraktionsstrategie ein Strategy Pattern ist: Dieselbe Entität, sechs Beschaffungswege, identisches Zielschema. Genau der Fall, für den das Muster gedacht ist.

### Stufe 3 – Echte Quellen mit API-Gegenprobe

Der stärkste Test, den es gibt, und der am häufigsten übersehene.

Manche Quellen bieten dieselben Daten über eine offizielle API *und* als HTML-Seite an. Dann liefert die API kostenlos die Grundwahrheit: Der HTML-Extraktor läuft, die API läuft, beide Ergebnisse werden verglichen. Jede Abweichung ist ein Extraktionsfehler – automatisch erkannt, ohne dass jemand ein Erwartungsergebnis pflegen muss. Ein sich selbst überwachender Scraper.

Geeignet sind unter anderem Wikipedia und Wikidata, Hacker News (HTML plus Firebase-API), GitHub (HTML plus REST-API), sowie im Fachbereich ClinicalTrials.gov, ChEMBL, Open Targets, PubMed und openFDA – für die H2H-Nachbarschaft ohnehin bekanntes Terrain. Für Schweizer Kontext: `opendata.swiss`, MeteoSchweiz, `opentransportdata.swiss`, Zefix.

Diese Stufe gehört in einen nächtlichen Lauf, nicht in CI, und ist der Härtetest für die Behauptung „unsere Daten stimmen".

### Stufe 4 – Echte Quellen, JavaScript-schwer und volatil

Hier geht es um die Fetch-Schicht und um Realismus des Mutationskatalogs.

Statt zu raten, welche Seite sich oft ändert, gibt es ein belastbares Erkennungsmerkmal: **generierte Klassennamen.** Sieht das Markup aus wie `css-1x2y3z`, `Product_price__3kJd9` oder `sc-bdVaJa`, dann stammt es aus CSS-in-JS, CSS Modules oder Tailwind-JIT – und die Klassennamen rotieren bei jedem Build. Solche Seiten brechen konstruktionsbedingt im Wochentakt. Das ist kein Zufallsfund, sondern ein Auswahlkriterium.

Zweites Merkmal: Next.js- oder Nuxt-Seiten mit `__NEXT_DATA__` beziehungsweise `__NUXT__`. Deren DOM ist volatil, aber der eingebettete JSON-Zustand ist stabil. Perfekt, um zu zeigen, dass die Extraktionsreihenfolge aus `01-architecture.md` wirkt: Der CSS-Pfad bricht ständig, der Embedded-JSON-Pfad überlebt.

Auswahlregeln für diese Stufe: nur öffentlich zugängliche Inhalte, ToS und robots.txt vorher prüfen, sehr niedrige Rate Limits, keine personenbezogenen Daten, und bevorzugt Betreiber mit Open-Data-Auftrag. Details in `COMPLIANCE.md`.

### Nicht anfassen

Amazon, LinkedIn, Instagram, Ticketplattformen und alles mit Enterprise-Bot-Schutz. Dort lernt man nichts über Self-Healing, sondern nur über ein Proxy-Wettrüsten, das nicht zu gewinnen ist – zusätzlich zu ToS-Verstößen, die ein Portfolioprojekt untauglich machen.

## Mutationskatalog

Angewandt auf echte Snapshots, mit fest hinterlegtem Sollverhalten. Das erwartete Ergebnis ist Teil des Tests, nicht das Ergebnis selbst.

### Echte Brüche – Erwartung `auto_healed`

| ID | Mutation | Zielstufe |
|---|---|---|
| M01 | Klassenname umbenannt (`.price_color` → `.price_color_v2`) | Wertanker |
| M02 | Hash-Klasse rotiert (`css-1a2b3c` → `css-9x8y7z`) | Wertanker |
| M03 | Zusätzliches Wrapper-Div eingezogen, Tiefe +1 | Wertanker |
| M04 | Geschwisterreihenfolge getauscht, `nth-child` verschoben | Wertanker |
| M05 | Tag ausgetauscht (`span` → `div`) | Wertanker |
| M06 | Feld aufgespalten (`CHF 49.90` → zwei Elemente) | DOM-Diff |
| M07 | Felder zusammengeführt | DOM-Diff |
| M08 | Formatwechsel (`49.90` → `49,90`; ISO-Datum → `TT.MM.JJJJ`) | Transform-Anpassung |
| M09 | Attribut statt Textinhalt (`data-price="49.90"`) | DOM-Diff |
| M10 | Paginierungsschema geändert (`?page=2` → `?cursor=abc`) | Discovery-Heilung |
| M11 | Umstellung auf Client-Rendering, HTML leer | Fetch-Strategie-Wechsel |
| M12 | Vollständiges Redesign, Struktur unkenntlich | LLM |

### Fachliche Änderungen – Erwartung `escalated`

| ID | Mutation | Begründung |
|---|---|---|
| M13 | Feld vollständig entfernt | Schema-Entscheidung, keine Reparatur |
| M14 | Neues Pflichtfeld erschienen | Vertragserweiterung |
| M15 | Natural Key nicht mehr extrahierbar | korrumpiert Bestand rückwirkend |
| M16 | Enum-Wert außerhalb der Deklaration | Semantikänderung |

### Negativfälle – Erwartung `no_action`

| ID | Situation | Falsches Verhalten wäre |
|---|---|---|
| N01 | Consent-Wall mit Status 200 statt Inhalt | Selektoren aus dem Cookie-Banner lernen |
| N02 | Soft-404 mit Status 200 | Extraktor gegen eine Fehlerseite umschreiben |
| N03 | A/B-Test, Inhalt identisch, Reihenfolge anders | funktionierenden Selektor ersetzen |
| N04 | 429 vom Rate Limiter | Bruch vermuten statt Backoff erhöhen |
| N05 | Teilausfall, 30 % der Seiten leer | Gesamtreparatur auf Basis von Teilinformation |
| N06 | Quelle liefert legitim weniger Zeilen (Saison) | Discovery „reparieren", die korrekt arbeitet |

## Bewertung

Der Harness gibt zwei Zahlen aus, beide bei jedem CI-Lauf:

```
Heilungsrate    11/12 echte Brüche automatisch behoben     (M01–M12)
Eskalationsrate  4/4  fachliche Änderungen korrekt erkannt (M13–M16)
Fehlalarmrate    0/6  Negativfälle fälschlich behandelt    (N01–N06)
```

Die Fehlalarmrate ist die wichtigste der drei. Ein System mit 90 % Heilung und 20 % Fehlalarm ist schlechter als eines mit 70 % und null – weil ein Fehlalarm einen funktionierenden Extraktor zerstört und den Schaden als aktive Version festschreibt.

Jeder in der Workbench als Fehlalarm geschlossene Incident wird automatisch als neuer Negativfall in den Katalog aufgenommen. Der Bestand wächst mit den realen Irrtümern des Systems.

## Umsetzung

Die Mutationen greifen an einem geparsten Baum an, nicht per Regex auf Text. Jede Mutation ist eine Funktion `(HTML, Seed) → HTML` mit festem Seed für Reproduzierbarkeit.

```python
@mutation(id="M03", expect="auto_healed")
def wrapper_insert(tree: HTMLParser, seed: int) -> HTMLParser:
    """Zieht ein zusätzliches Div um das Zielelement, Tiefe +1."""
```

Als pytest-Parametrisierung über das Kreuzprodukt aus Quellen und Mutationen. Bei drei Quellen und zweiundzwanzig Mutationen sind das sechsundsechzig Fälle in wenigen Sekunden – die Grundlage dafür, das Self-Healing überhaupt iterativ entwickeln zu können statt zu raten.
