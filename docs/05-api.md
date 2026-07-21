# 05 – API

Read-only über den Gold-Layer. Sobald ein externer Dienst daran hängt, ist das Schema ein Vertrag – deshalb ab dem ersten Tag versionierter Pfad, auch wenn es zunächst nur einen Konsumenten gibt.

## Vertragsquelle

Dasselbe Pydantic-Modell, das den Extraktionsvertrag definiert und die Validierung durchführt, erzeugt das Response-Schema und damit OpenAPI. Es gibt keine zweite Definition, die auseinanderlaufen könnte. Eine Feldänderung im Site-Pack schlägt sofort im generierten Client des Konsumenten auf – was gewollt ist, weil das Alternative eine stille Inkompatibilität wäre.

## Endpunkte

```
GET  /v1/health                     Liveness, keine Auth
GET  /v1/ready                      Readiness inkl. DB-Verbindung
GET  /metrics                       Prometheus, nur intern erreichbar

GET  /v1/{entity}                   Liste, Cursor-paginiert
GET  /v1/{entity}/{natural_key}     Einzelabruf
GET  /v1/{entity}/{natural_key}/history   Änderungshistorie aus Silver
GET  /v1/{entity}/changes           Change-Feed ab Cursor
GET  /v1/sources                    Quellen mit Status, letzter Lauf, Extraktorversion
```

## Paginierung

Cursor auf `(valid_from, id)`, nicht Offset. Bei einem Datenbestand, der sich während der Iteration ändert, überspringt oder dupliziert Offset-Paginierung Zeilen – bei einem Change-Feed ist das ein Korrektheitsfehler, kein Performanceproblem.

```
GET /v1/book?limit=100&cursor=eyJ0IjoiMjAyNi0wNy0yMVQxMDoxNTowMFoiLCJpIjoiOWY4Ny4uLiJ9
```

```json
{
  "data": [ ... ],
  "meta": {
    "next_cursor": "eyJ0IjoiMjAyNi0wNy0yMVQxMDoxODoxMloi...",
    "has_more": true,
    "extractor_version": 3,
    "generated_at": "2026-07-21T10:22:41Z"
  }
}
```

`has_more` statt `total`. Ein `COUNT(*)` über eine große Tabelle bei jeder Seitenabfrage ist teuer und für den Konsumenten meist wertlos.

## Konsumentenfreundlichkeit

**Feldprojektion** – `?fields=title,price` reduziert Nutzlast und macht den Vertrag expliziter. Wer nur zwei Felder anfordert, ist von der Änderung eines dritten nicht betroffen.

**Conditional Requests** – `ETag` über den Payload-Hash der Antwort plus `Last-Modified`. Ein Konsument, der minütlich pollt, bekommt in der Regel `304 Not Modified` und kostet praktisch nichts.

**Change-Feed statt Polling** – `GET /v1/{entity}/changes?since={cursor}` liefert nur, was sich seit dem Cursor geändert hat. Direkt aus dem SCD-Typ-2-Bestand, kein zusätzlicher Mechanismus nötig. Optional zusätzlich Webhooks mit HMAC-Signatur, at-least-once, Idempotenzschlüssel je Ereignis.

**Ehrlichkeit über Datenalter** – jede Antwort trägt `generated_at` und `extractor_version`. `GET /v1/sources` macht sichtbar, ob eine Quelle gerade einen offenen Incident hat und wie alt der letzte erfolgreiche Lauf ist. Ein Konsument muss erkennen können, dass er möglicherweise mit veralteten Daten arbeitet, ohne dafür Grafana aufzurufen.

## Auth und Limits

API-Keys mit Scopes pro Entität, gehasht in der Datenbank abgelegt. Rate Limits pro Key mit `X-RateLimit-*`-Headern und `Retry-After` bei 429. Bei 5xx keine internen Details in der Antwort; jede Fehlerantwort trägt eine `request_id`, über die sich die Details in den Logs finden lassen.

## Versionierung

Rückwärtskompatible Änderungen – neue Felder, neue optionale Parameter – erfolgen innerhalb von `v1`. Feld entfernen, Typ ändern, Semantik ändern erzeugt `v2`; `v1` bleibt mindestens 90 Tage parallel bestehen und liefert `Deprecation` und `Sunset` als Header. Feldweise Abkündigung wird zusätzlich in `meta.deprecations` angekündigt, damit der Konsument den Umstieg planen kann.

## Was bewusst fehlt

Kein Schreibzugriff. Kein GraphQL – bei einer Handvoll Entitäten mit flachen Feldern ist der Aufwand nicht gerechtfertigt, und die Feldprojektion deckt den eigentlichen Bedarf ab. Keine Aggregationsendpunkte; wer Analytik braucht, bekommt den Parquet-Export und rechnet mit DuckDB, statt die Betriebsdatenbank zu belasten.
