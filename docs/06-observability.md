# 06 – Observability

## Aufteilung

Zeitreihen, Dashboards und Alarmrouting sind gelöste Probleme: Prometheus, Grafana, Alertmanager. Das nachzubauen wäre verschwendete Zeit.

Selbst gebaut wird nur, was zugekaufte Werkzeuge nicht können: die **Incident-Workbench**. Grafana zeigt, *dass* die Fill-Rate eingebrochen ist. Es zeigt nicht den DOM-Diff, den abgelehnten Reparaturvorschlag und einen Knopf, der den Fix gegen die Fixtures testet und übernimmt. Genau das ist der Unterschied zwischen zwanzig Minuten und vier Minuten Behebungszeit.

## Metriken

```
kintsugi_run_duration_seconds{domain,entity,status}          histogram
kintsugi_pages_fetched_total{domain,fetcher,http_status}     counter
kintsugi_rows_extracted_total{domain,entity}                 counter
kintsugi_field_fill_rate{domain,entity,field}                gauge
kintsugi_range_violations_total{domain,entity,field}         counter
kintsugi_row_count_deviation{domain,entity}                  gauge
kintsugi_duplicate_rate{domain,entity}                       gauge
kintsugi_probe_reachable{domain}                             gauge   (unabhängig vom Lauf)
kintsugi_probe_latency_seconds{domain}                       histogram
kintsugi_heal_attempts_total{domain,stage,outcome}           counter
kintsugi_heal_gate_rejections_total{domain,reason}           counter
kintsugi_active_pack_version{domain,entity}                  gauge
kintsugi_incidents_open{severity}                            gauge
kintsugi_incident_ttr_seconds{kind,resolution}               histogram
kintsugi_llm_cost_usd_total{purpose}                         counter
```

Zwei davon sind die eigentlichen Steuergrößen. `kintsugi_field_fill_rate` ist der Frühindikator für alles, was schiefgeht. `kintsugi_incident_ttr_seconds` ist die Zielmetrik des gesamten Systems.

`kintsugi_probe_reachable` kommt aus dem unabhängigen Synthetic Probe, nicht aus dem Scrape-Lauf. Diese Trennung ist der Grund, warum sich „Quelle ausgefallen" von „unser Parser kaputt" unterscheiden lässt.

## Dashboards

**Flottenübersicht** – eine Kachel pro Quelle: Ampel, letzter erfolgreicher Lauf, aktive Extraktorversion, offene Incidents. Die Frage „läuft alles" muss in drei Sekunden beantwortet sein.

**Quellendetail** – Fill-Rate pro Feld über 30 Tage mit eingezeichneten Versionswechseln. Der Zusammenhang zwischen einem Versionswechsel und einer Metrikänderung ist die wichtigste diagnostische Information überhaupt und muss visuell offensichtlich sein.

**Heilungsbilanz** – Reparaturversuche nach Stufe und Ergebnis, Gate-Ablehnungen nach Grund, Anteil maschinell erzeugter aktiver Versionen, Fehlalarmrate. Das ist die ehrliche Selbstauskunft des Systems über die Qualität seiner Automatik.

**Kosten** – LLM-Ausgaben, Proxy-Traffic, Browser-Sekunden je Quelle. Playwright ist teuer; ohne diese Sicht schleicht sich Browser-Fetch dort ein, wo HTTP gereicht hätte.

## Alarmstufen

Wer bei allem alarmiert, wird ignoriert. Drei Stufen, klar getrennt:

| Stufe | Fälle | Kanal |
|---|---|---|
| `info` | automatisch geheilt, Canary bestanden; neues Feld entdeckt; legitime Mengenänderung | nur Log und Dashboard |
| `warn` | geheilt, aber Canary auffällig; Teilausfall; Rate-Limit erreicht; Quelle 24 h nicht erreichbar | Telegram oder ntfy, gesammelt |
| `critical` | Heilung fehlgeschlagen; Schema-Änderung vermutet; Natural Key gebrochen; Kontingent erschöpft; API liefert 5xx | sofortige Benachrichtigung |

Zusätzlich Dämpfung: Alarme werden pro Quelle gruppiert, wiederholte Meldungen desselben Incidents unterdrückt, und während eines bekannten Ausfalls einer Quelle werden Folgemeldungen für dieselbe Quelle stummgeschaltet.

## Incident-Workbench

Das eigentlich zu bauende Stück. Ein Incident öffnet sich mit allem, was zur Entscheidung nötig ist, ohne einen einzigen Klick woandershin.

**Kopf** – Quelle, Entität, betroffenes Feld, Art, Zeitpunkt, aktive Versionsnummer, offen seit.

**Beweislage**
- Fill-Rate des Felds über 30 Tage, Bruchstelle markiert
- drei Beispielzeilen vorher, drei nachher, Unterschiede hervorgehoben
- DOM-Diff des letzten Good-Snapshots gegen den aktuellen, geänderte Region hervorgehoben, kollabiert bis auf den relevanten Teilbaum
- Historie: wie oft ist diese Quelle in 90 Tagen gebrochen, mit welchen Ursachen

**Vorschlag** – falls die Heilung einen Kandidaten erzeugt hat: alter Selektor, vorgeschlagener Selektor, Ergebnis des Fixture-Replays feldweise, und falls abgelehnt, der genaue Ablehnungsgrund.

**Werkbank**
- Selektor-Editor mit Sofortauswertung gegen den aktuellen Snapshot, Trefferzahl und extrahierte Werte live
- Ein-Klick-Replay gegen alle Golden Fixtures, Ergebnis in unter zwei Sekunden
- Direktsprung in den Roh-Snapshot mit Syntaxhervorhebung
- Vorschau: „welche Zeilen im Gold-Layer würde dieser Fix ändern"

**Aktionen**
- KI-Vorschlag übernehmen
- eigenen Selektor speichern und als neue Version aktivieren
- als Schema-Änderung markieren – öffnet den Migrationspfad statt der Reparatur
- Feld abkündigen
- als Fehlalarm schließen – erzeugt automatisch einen Negativtest im Mutations-Harness
- auf die Vorversion zurückrollen

Der vorletzte Punkt ist der wertvollste Rückkopplungskreis im System. Jeder als Fehlalarm geschlossene Incident wird zu einem dauerhaften Regressionstest. Das System wird messbar besser, ohne dass irgendetwas trainiert werden müsste.

## Betriebsziele

| Größe | Ziel |
|---|---|
| Anteil automatisch geheilter Brüche | über 70 % |
| Fehlalarmrate der Heilung | 0 % auf dem Negativtestbestand |
| MTTR bei Eskalation | unter 15 Minuten |
| Zeit vom Bruch bis zum Alarm | unter einem Laufintervall |
| Stille Datenfehler, die die API erreichen | 0 |

Der letzte Punkt ist der einzige, bei dem es keinen akzeptablen Zielwert über null gibt. Lieber liefert die API sichtbar veraltete Daten mit Warnung, als still falsche.
