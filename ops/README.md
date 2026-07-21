# ops/

Betriebsartefakte: `docker-compose`, Prometheus-Regeln, Grafana-Dashboards, Alarmrouting.

Siehe [`docs/06-observability.md`](../docs/06-observability.md).

| Inhalt | Phase |
|---|---|
| `docker-compose.yml` mit PostgreSQL 16 | 0 |
| Prometheus-Regeln und Grafana-Dashboards | 3 |
| Alertmanager-Routing über ntfy oder Telegram | 3 |

Bis Phase 3 bleibt hier nur der Datenbank-Stack für die lokale Entwicklung.
