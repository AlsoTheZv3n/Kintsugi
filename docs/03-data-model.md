# 03 – Datenmodell

## Ebenen

**Bronze** – rohe Antworten, gzip-komprimiert im Objektspeicher, Metadaten in Postgres. Immutable. Das ist die Grundlage für Golden Fixtures, DOM-Diffs und jede Form von Reproduzierbarkeit. Wer diese Ebene weglässt, kann nicht heilen und kann Fehler nicht nachvollziehen.

**Silver** – extrahiert, validiert, normalisiert. Typisierter Kern als Spalten, variabler Teil als JSONB. Historisiert als Slowly Changing Dimension Typ 2.

**Gold** – die aktuell gültige Sicht, die die API ausliefert. Eine View, keine kopierte Tabelle. Wird sie zu langsam, eine Materialized View mit `REFRESH CONCURRENTLY`.

Daneben ein Parquet-Export für Analytik, damit DuckDB-Auswertungen und Baseline-Profile die Betriebsdatenbank nicht belasten.

## Warum Postgres und nicht MongoDB

Die Daten sind semi-strukturiert, aber die Beziehungen sind es nicht. Snapshot gehört zu Lauf gehört zu Site-Pack-Version, Record verweist auf beides – das ist relational, und die Provenance ist der halbe Wert des Systems. `JSONB` mit GIN-Index deckt den variablen Teil vollständig ab, und Constraints, Fremdschlüssel, partielle Unique-Indizes und transaktionale Versionswechsel gibt es dazu. Zwei Datenbanken für einen Anwendungsfall wären reine Betriebslast.

## DDL

```sql
CREATE TYPE site_pack_status  AS ENUM ('draft','canary','active','retired','rejected');
CREATE TYPE run_trigger       AS ENUM ('schedule','manual','canary','replay');
CREATE TYPE run_status        AS ENUM ('running','ok','degraded','failed');
CREATE TYPE incident_severity AS ENUM ('info','warn','critical');
CREATE TYPE incident_kind     AS ENUM (
    'fill_rate_drop','row_count_anomaly','range_violation','schema_change',
    'field_removed','unreachable','blocked','rate_limited','healer_exhausted');
CREATE TYPE incident_resolution AS ENUM (
    'auto_healed','human_fixed','schema_migrated','false_positive','source_recovered');
```

### Site-Packs

```sql
CREATE TABLE site_pack (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    domain         text NOT NULL,
    entity         text NOT NULL,
    version        integer NOT NULL,
    status         site_pack_status NOT NULL DEFAULT 'draft',
    spec           jsonb NOT NULL,
    parent_version integer,
    created_at     timestamptz NOT NULL DEFAULT now(),
    created_by     text NOT NULL,          -- 'human:sven' | 'healer:v1'
    activated_at   timestamptz,
    retired_at     timestamptz,
    notes          text,
    UNIQUE (domain, entity, version)
);

CREATE UNIQUE INDEX site_pack_one_active
    ON site_pack (domain, entity) WHERE status = 'active';
CREATE UNIQUE INDEX site_pack_one_canary
    ON site_pack (domain, entity) WHERE status = 'canary';
```

### Läufe

```sql
CREATE TABLE run (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    site_pack_id   uuid NOT NULL REFERENCES site_pack(id),
    trigger        run_trigger NOT NULL,
    status         run_status NOT NULL DEFAULT 'running',
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    pages_fetched  integer NOT NULL DEFAULT 0,
    rows_extracted integer NOT NULL DEFAULT 0,
    metrics        jsonb NOT NULL DEFAULT '{}'::jsonb,
    error          text
);

CREATE INDEX run_pack_time ON run (site_pack_id, started_at DESC);
```

`metrics` enthält das Qualitätsprofil, das die Heilung auslöst:

```json
{
  "fill_rate":     {"title": 0.998, "price": 0.031, "upc": 1.0},
  "range_violations": {"price": 0},
  "row_count":     {"actual": 987, "median_14d": 1002, "deviation": -0.015},
  "duplicate_rate": 0.004,
  "http":          {"200": 987, "404": 13, "429": 0},
  "fetch_ms_p95":  842
}
```

### Bronze

```sql
CREATE TABLE snapshot (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       uuid NOT NULL REFERENCES run(id),
    url          text NOT NULL,
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    http_status  smallint NOT NULL,
    content_hash bytea NOT NULL,           -- sha256 des Rohkörpers
    content_type text,
    byte_size    integer NOT NULL,
    blob_key     text NOT NULL,            -- raw/<domain>/<yyyy>/<mm>/<sha256>.gz
    fetcher      text NOT NULL,            -- 'httpx' | 'playwright'
    is_golden    boolean NOT NULL DEFAULT false,
    golden_label text                      -- 'baseline' | 'edge:out_of_stock' | ...
);

CREATE INDEX snapshot_url_time  ON snapshot (url, fetched_at DESC);
CREATE INDEX snapshot_hash      ON snapshot (content_hash);
CREATE INDEX snapshot_golden    ON snapshot (run_id) WHERE is_golden;
```

Der `content_hash` erspart Speicherplatz und Rechenzeit: Ist der Hash identisch mit dem letzten Abruf derselben URL, wird kein neuer Blob geschrieben und keine Extraktion ausgeführt. Bei den meisten Quellen ändert sich der überwiegende Teil der Seiten zwischen zwei Läufen nicht.

`is_golden` markiert Snapshots, die zum Regressionsbestand gehören. Diese werden nie gelöscht und sind gegen die Retention ausgenommen.

### Silver

```sql
CREATE TABLE record (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity       text NOT NULL,
    natural_key  text NOT NULL,
    snapshot_id  uuid NOT NULL REFERENCES snapshot(id),
    site_pack_id uuid NOT NULL REFERENCES site_pack(id),
    valid_from   timestamptz NOT NULL DEFAULT now(),
    valid_to     timestamptz,              -- NULL = aktuell gültig
    payload      jsonb NOT NULL,
    payload_hash bytea NOT NULL,
    quality      jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX record_current
    ON record (entity, natural_key) WHERE valid_to IS NULL;
CREATE INDEX record_payload ON record USING gin (payload jsonb_path_ops);
CREATE INDEX record_changes ON record (entity, valid_from DESC);
```

SCD Typ 2 statt Überschreiben. Der Aufwand ist eine zusätzliche Spalte, der Gewinn ist erheblich: vollständige Änderungshistorie pro Entität, ein kostenloser Change-Feed für Webhooks (`WHERE valid_from > $cursor`), Zeitreisen-Abfragen für Debugging, und die Möglichkeit, nach einer schlechten Heilung genau die betroffenen Zeilen zu identifizieren und zurückzurollen.

Geschrieben wird nur bei tatsächlicher Änderung – `payload_hash` gleich bedeutet nur `valid_from` bestätigen, keine neue Zeile.

### Gold

```sql
CREATE VIEW gold_book AS
SELECT
    r.natural_key                       AS upc,
    r.payload ->> 'title'               AS title,
    (r.payload ->> 'price')::numeric    AS price,
    r.payload ->> 'currency'            AS currency,
    (r.payload ->> 'availability')::int AS availability,
    r.valid_from                        AS updated_at,
    sp.domain                           AS source_domain,
    sp.version                          AS extractor_version
FROM record r
JOIN site_pack sp ON sp.id = r.site_pack_id
WHERE r.entity = 'book' AND r.valid_to IS NULL;
```

`extractor_version` gehört bewusst in die ausgelieferte Nutzlast. Wenn ein Konsument merkwürdige Daten sieht, ist die erste Frage immer, welche Extraktorversion sie erzeugt hat.

### Incidents

```sql
CREATE TABLE incident (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    site_pack_id uuid NOT NULL REFERENCES site_pack(id),
    run_id       uuid REFERENCES run(id),
    kind         incident_kind NOT NULL,
    severity     incident_severity NOT NULL,
    field        text,
    opened_at    timestamptz NOT NULL DEFAULT now(),
    closed_at    timestamptz,
    resolution   incident_resolution,
    evidence     jsonb NOT NULL DEFAULT '{}'::jsonb,
    assignee     text
);

CREATE INDEX incident_open ON incident (severity, opened_at DESC) WHERE closed_at IS NULL;
```

`evidence` enthält alles, was die Workbench zum Öffnen braucht, ohne nachladen zu müssen: Snapshot-IDs vorher und nachher, betroffene Felder mit Fill-Rate vorher und nachher, drei Beispielzeilen aus beiden Zuständen, der DOM-Diff-Bereich und – falls vorhanden – der abgelehnte Heilungsvorschlag samt Ablehnungsgrund.

`resolution = 'false_positive'` ist der wertvollste Eintrag im ganzen Schema. Jeder so markierte Incident wird zu einem Negativtest im Mutations-Harness. Das System lernt aus seinen Fehlalarmen, ohne dass ein Modell trainiert werden müsste.

## Retention

| Ebene | Aufbewahrung |
|---|---|
| Bronze, `is_golden = true` | unbegrenzt |
| Bronze, regulär | 90 Tage, danach nur Hash und Metadaten |
| Silver, `valid_to IS NULL` | unbegrenzt |
| Silver, historisiert | 24 Monate |
| Runs und Metriken | 24 Monate |
| Incidents | unbegrenzt |

Bronze ist der größte Posten. Bei täglichem Abruf von 10'000 Seiten à 200 KB, komprimiert auf etwa 40 KB, und nur geänderten Seiten sind rund 5 GB pro Monat realistisch. Auf Standard-Objektspeicher vernachlässigbar.
