# 01 – Architektur

## Problem

Web-Daten in einen typisierten, versionierten Bestand überführen, den ein anderer Dienst über eine API konsumieren kann – und diesen Betrieb aufrechterhalten, obwohl sich die Quellen unangekündigt ändern. Der Betriebsaufwand, nicht die Extraktion, ist das eigentliche Problem: Ein Scraper zu schreiben dauert eine Stunde, zwanzig Scraper am Leben zu halten kostet dauerhaft.

## Nicht-Ziele

- Kein eigenes Proxy-Netzwerk. Residential-IPs werden bei Bedarf zugekauft und hinter einem Adapter versteckt.
- Kein Wettrüsten gegen Enterprise-Bot-Schutz. Ziele mit aggressivem Anti-Bot sind explizit außerhalb des Scopes (siehe `07-test-targets.md`).
- Kein Zugriff hinter Logins, keine personenbezogenen Daten ohne dokumentierte Rechtsgrundlage (siehe `COMPLIANCE.md`).
- Keine Echtzeit. Zieltakt ist minütlich bis täglich, nicht Sub-Sekunde.

## Komponenten

### Orchestrator
Plant Läufe, verteilt Arbeit, hält Zustand. Phase 1 ein schlanker Postgres-basierter Job-Runner, ab Phase 5 Dagster. Dagsters Asset-Modell mit Asset Checks und Freshness Policies deckt einen Teil der Qualitätsüberwachung ab Werk ab; bis dahin wäre es Overhead.

### Discovery
Ermittelt die zu holenden URLs. Strategien: Sitemap, Paginierung, Seed-Liste, offizielle API. Getrennt vom Fetch, weil sich Paginierungsschemata unabhängig vom Seitenlayout ändern und getrennt heilbar sein müssen.

### Fetch (Strategy)
`HttpFetcher` (httpx, HTTP/2, günstig) und `BrowserFetcher` (Playwright, teuer) hinter einem gemeinsamen Protokoll. Der Fetcher ist eine Eigenschaft des Site-Packs, keine globale Einstellung, und kann durch Heilung gewechselt werden, wenn eine Quelle von Server- auf Client-Rendering umstellt. Zuständigkeiten: Robots-Prüfung, Rate Limiting pro Domain, Retry mit Backoff, Conditional Requests via ETag, optionaler Proxy-Adapter.

Jede Antwort wird als Snapshot persistiert, bevor irgendetwas geparst wird. Ohne Snapshots gibt es keine Golden Fixtures, keinen Diff und damit keine Heilung.

### Extraction (Strategy, priorisiert)
Nacheinander, erster Treffer gewinnt:

1. **Offizielle API** – falls vorhanden, immer bevorzugt.
2. **JSON-LD / schema.org** – strukturiert, semantisch, ändert sich selten.
3. **Eingebettetes JSON** – `__NEXT_DATA__`, `__NUXT__`, Redux-Preload-State. Deutlich stabiler als DOM-Selektoren.
4. **XHR-Endpunkt der Seite** – die Seite lädt ihre Daten meist selbst per JSON. Diese Endpunkte brechen um Größenordnungen seltener als Klassennamen.
5. **CSS/XPath-Selektoren** – der fragile Fallback.
6. **LLM-Extraktion** – nur zur Erzeugung eines Selektors, niemals pro Seite im Betrieb.

Die Reihenfolge ist der wichtigste Hebel für Wartungsarmut. Wer bei Stufe 5 anfängt, baut sich seine Heilungslast selbst.

### Validation
Ein Pydantic-Modell pro Entität ist gleichzeitig Extraktionsvertrag, Datenbank-Validierung und API-Response-Schema. Eine Änderung propagiert konsistent durch alle drei; OpenAPI fällt automatisch ab.

Die Validierung produziert nicht nur Pass/Fail, sondern ein Qualitätsprofil pro Lauf: Fill-Rate je Feld, Typverletzungen, Wertebereichsverletzungen, Zeilenzahl gegen historischen Median, Verteilungsdrift. Dieses Profil ist der Auslöser für Heilung.

### Normalization
Währungen, Einheiten, Datum mit Zeitzone, Unicode-Normalisierung, HTML-Entities, Whitespace. Deklarativ als Transform-Kette im Site-Pack, damit Formatänderungen ohne Codeänderung geheilt werden können.

### Storage
Drei Ebenen, siehe `03-data-model.md`. Kurz: Bronze sind rohe Snapshots im Objektspeicher, Silver ist Postgres mit typisiertem Kern plus JSONB, Gold sind die aktuell gültigen Datensätze für die API.

### Self-Healing
Eigener Dienst, siehe `04-self-healing.md`. Konsumiert Qualitätsprofile, produziert entweder eine neue Site-Pack-Version oder einen Incident.

### API
FastAPI über Gold, read-only, versionierter Pfad. Siehe `05-api.md`.

### Observability
Prometheus plus Grafana plus Alertmanager für Zeitreihen und Alarmrouting. Eigenbau nur für die Incident-Workbench. Siehe `06-observability.md`.

## Datenfluss

```
Discovery ──► Fetch ──► Snapshot (Bronze)
                          │
                          ▼
                      Extraction ──► Validation ──► Normalization ──► Record (Silver)
                          ▲              │                                  │
                          │              │ Qualitätsprofil                  ▼
                          │              ▼                              Gold (View)
                          │        Self-Healing                             │
                          │         ├─ geheilt ──► neue Site-Pack-Version ──┘
                          └─────────┤
                                    └─ eskaliert ──► Incident ──► Workbench + Alarm
```

Zwei Punkte sind nicht verhandelbar:

**Der Snapshot entsteht vor dem Parsing.** Sonst ist jeder Bruch unreproduzierbar und die Heilung blind.

**Erreichbarkeit wird getrennt gemessen.** Ein eigenständiger Synthetic Probe pro Domain, entkoppelt vom Scrape-Lauf. Ohne ihn lässt sich „Quelle ist ausgefallen" nicht von „unser Parser ist kaputt" unterscheiden – und das System heilt gegen eine Cloudflare-Fehlerseite, zerstört dabei einen funktionierenden Selektor und schreibt den Schaden als neue aktive Version fest. Das ist der wahrscheinlichste Selbstzerstörungspfad des Systems.

## Erweiterbarkeit

Ein Plugin-System ist hier klar gerechtfertigt. Die Kriterien treffen zu: Erweiterung durch Dritte (jede Domain ist ein Site-Pack), strukturgleiche Fälle bei inhaltlicher Vielfalt (jede Quelle durchläuft dieselbe Pipeline), getrennte Versionierung nötig (Site-Pack-Versionen müssen sich unabhängig vom Kern bewegen – erst das macht Heilung sicher). Gegenkriterien greifen nicht: Die Fallmenge ist offen, es gibt kein typisiertes Cross-Entity-Reasoning, und es wird sehr schnell mehr als eine Implementierung pro Erweiterungspunkt geben.

Erweiterungspunkte:

| Punkt | Muster | Implementierungen |
|---|---|---|
| `Fetcher` | Strategy | httpx, Playwright, Proxy-Provider |
| `DiscoveryStrategy` | Strategy | Sitemap, Paginierung, Seed, API |
| `Extractor` | Strategy | API, JSON-LD, embedded JSON, XHR, CSS, LLM |
| `Transform` | Chain | strip, currency, date, unit, regex |
| `Sink` | Adapter | Postgres, S3/Parquet, Webhook |
| `Notifier` | Adapter | ntfy, Telegram, E-Mail, Slack |
| `SitePack` | deklarative Konfiguration | eine pro Domain und Entität |

Site-Packs bleiben deklarativ. Ein Python-Hook ist als Escape Hatch vorgesehen, aber jede Nutzung ist ein Signal, dass eine Transform-Primitive fehlt.

## Stack

Python 3.12, Abhängigkeiten ausschließlich über `uv`. httpx, Playwright, selectolax (deutlich schneller als BeautifulSoup), jsonpath-ng, Pydantic v2, Pandera für Qualitäts-Assertions, ydata-profiling für periodische Tiefenreports und das Baseline-Profil. Postgres 16, SeaweedFS als Objektspeicher (MinIO ist EOL). FastAPI. Grafana, Prometheus, Alertmanager. Incident-Workbench als schlankes Next.js-Frontend. Container nach den Hausregeln: Multi-Stage, Abhängigkeiten vor App-Code, `uv sync --frozen --no-dev`, Non-Root `appuser`, HEALTHCHECK über Python-urllib mit `--start-period`.
