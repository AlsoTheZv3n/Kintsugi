# 02 – Site-Packs

Ein Site-Pack beschreibt eine Quelle vollständig deklarativ: wo die URLs herkommen, wie geholt wird, wie extrahiert wird, was gelten muss und wie viel Autonomie die Heilung hat. Es ist eine Zeile in Postgres mit Versionsnummer, kein Python-Modul.

## Warum Daten statt Code

Kommerzielle Anbieter lassen die KI generierten Python-Code umschreiben und deployen. Das ist der riskanteste Punkt an solchen Systemen: Der Änderungsraum ist unbegrenzt, ein Diff ist schwer zu beurteilen, und ein Rollback bedeutet Redeployment.

Ist die Änderungsoberfläche dagegen ein Schema-validiertes Dokument, gilt: Der Vorschlag lässt sich vor der Ausführung prüfen, jede Version ist diffbar und auditierbar, Rollback ist ein `UPDATE` auf den Status, und der Explosionsradius eines schlechten Vorschlags ist auf einen Selektorstring begrenzt. Ein Modell, das JSON gegen ein Schema füllt, ist auch schlicht zuverlässiger als eines, das lauffähigen Code schreibt.

## Beispiel

```yaml
apiVersion: kintsugi/v1
domain: books.toscrape.com
entity: book
version: 3

discovery:
  strategy: sitemap            # sitemap | pagination | seed_list | api
  sitemap_url: https://books.toscrape.com/sitemap.xml
  url_pattern: '^https://books\.toscrape\.com/catalogue/[^/]+/index\.html$'
  max_urls_per_run: 1000

fetch:
  strategy: http               # http | browser
  rate_limit_rps: 0.5
  concurrency: 2
  respect_robots: true
  conditional_requests: true   # ETag / If-Modified-Since
  proxy_pool: null             # null | residential | datacenter
  browser:
    wait_for: null
    block_resources: [image, font, media]

extract:
  sources:                     # Reihenfolge ist Priorität, erster Treffer gewinnt
    - kind: jsonld
      type: Product
    - kind: embedded_json
      script_id: __NEXT_DATA__
      root: props.pageProps.product
    - kind: css
      row_selector: null       # null = eine Entität pro Seite
      fields:
        title:
          selector: 'div.product_main > h1'
          anchor_hint: 'Der Buchtitel, die erste Überschrift im Produktbereich'
          transform: [strip]
        price:
          selector: 'p.price_color'
          anchor_hint: 'Preis mit Währungssymbol, z. B. £51.77'
          transform: [strip, parse_currency]
        availability:
          selector: 'p.availability'
          anchor_hint: 'Text wie "In stock (22 available)"'
          transform: [strip, int_from_text]
        upc:
          selector: 'table.table-striped tr:nth-child(1) td'
          anchor_hint: 'UPC in der Produktinformationstabelle, erste Zeile'

schema:
  natural_key: [upc]
  fields:
    title:        {type: string,  required: true,  min_fill_rate: 0.99}
    price:        {type: decimal, required: true,  min_fill_rate: 0.98, sane_range: [0.01, 10000]}
    currency:     {type: string,  required: true,  enum: [GBP, CHF, EUR, USD]}
    availability: {type: integer, required: false, min_fill_rate: 0.80, sane_range: [0, 100000]}
    upc:          {type: string,  required: true,  min_fill_rate: 1.0, pattern: '^[a-f0-9]{16}$'}

quality:
  min_rows_per_run: 200
  row_count_deviation: 0.30    # Abweichung vom 14-Tage-Median, die einen Incident auslöst
  max_duplicate_rate: 0.02

healing:
  enabled: true
  max_auto_versions_per_window: 3
  window: 7d
  require_golden_pass: true
  canary_fraction: 0.05
  canary_min_rows: 50
  escalate_on: [field_removed, schema_change, enum_violation, natural_key_broken]

delivery:
  sinks: [postgres]
  webhook_on_change: null
```

## Feldsemantik

**`anchor_hint`** ist der einzige Freitext im Dokument und existiert ausschließlich für die Heilung. Wenn ein Selektor bricht, ist die Beschreibung dessen, was das Feld fachlich bedeutet, für das LLM wertvoller als der kaputte Selektor. Ein Hinweis wie „Preis mit Währungssymbol" macht den Unterschied zwischen einem brauchbaren und einem geratenen Vorschlag.

**`min_fill_rate`** ist der eigentliche Wachhund. Ein Feld mit 99 % erwarteter Füllrate, das auf 3 % fällt, ist kaputt – auch wenn kein Fehler geworfen wurde. Der Wert wird beim Anlegen aus einem Baseline-Lauf abgeleitet, nicht geraten.

**`sane_range`** fängt die Klasse von Brüchen ab, bei denen der Selektor noch trifft, aber das Falsche: Ein Selektor, der nach einem Redesign die Artikelnummer statt des Preises greift, liefert weiterhin eine Zahl. Die Bereichsprüfung merkt es, der Typcheck nicht.

**`natural_key`** ist der stabile Identifikator auf der Quelle. Ohne ihn gibt es keine Deduplizierung, keine Änderungshistorie und kein SCD-Typ-2 (siehe `03-data-model.md`). Bricht der Natural Key, wird immer eskaliert und nie automatisch geheilt – ein falscher Schlüssel korrumpiert den gesamten Bestand rückwirkend.

**`max_auto_versions_per_window`** ist die Notbremse. Drei automatische Reparaturen einer Quelle in sieben Tagen bedeuten nicht, dass die Heilung gut funktioniert, sondern dass etwas Grundsätzliches nicht stimmt – instabile Zielseite, zu enge Schwellwerte oder ein Heiler, der sich im Kreis dreht. Danach übernimmt ein Mensch.

## Lebenszyklus einer Version

```
draft ──► canary ──► active ──► retired
  │          │
  │          └── Canary-Statistik auffällig ──► rejected
  └── Golden-Fixture-Gate nicht bestanden ──► rejected
```

Pro `(domain, entity)` ist genau eine Version `active`. Das wird in der Datenbank erzwungen, nicht in der Anwendung:

```sql
CREATE UNIQUE INDEX site_pack_one_active
    ON site_pack (domain, entity)
    WHERE status = 'active';
```

Jede Version trägt `created_by`, entweder `human:sven` oder `healer:v1`. Das ist keine Kosmetik: Die Auswertung „wie viele aktive Versionen stammen von der Maschine und wie viele davon mussten später korrigiert werden" ist die einzige belastbare Aussage über die Qualität der Heilung.

## Validierung des Site-Packs selbst

Das Dokument wird gegen ein JSON-Schema geprüft, das aus einem Pydantic-Modell generiert wird. Zusätzlich statisch vor der Übernahme:

- Selektoren sind syntaktisch parsebar
- alle in `schema.fields` deklarierten Felder haben eine Extraktionsquelle
- `natural_key` verweist auf existierende Pflichtfelder
- Transform-Ketten sind typverträglich (`parse_currency` nach `int_from_text` ist ein Fehler)
- `min_fill_rate` eines Pflichtfelds ist nicht kleiner als 0.5

Diese Prüfungen laufen auch auf KI-Vorschlägen, vor dem Golden-Fixture-Gate. Der billigste abgelehnte Vorschlag ist der, für den nie ein Fixture-Replay gestartet wurde.
