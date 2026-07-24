"""Der synchrone Phase-0-Runner: Discovery bis Silver, ein Lauf, kein Scheduler.

docs/08-roadmap.md §Phase 0 ("synchroner Runner, kein Scheduler") und
docs/01-architecture.md §Datenfluss. Schritte in dieser Reihenfolge: aktives
Pack aufloesen -> ``run``-Zeile ``status='running'`` -> Discovery ueber die
Registry -> je URL robots/Rate/Fetch, ``save_snapshot`` (Snapshot vor Parsing),
versionsbewusster unchanged/304-Kurzschluss, Block-Erkennung, Extraktion,
Transform, zeilenweise Validierung, Sammeln -> ein ``write_records`` mit **einem**
laufweiten ``valid_from`` -> ``run`` schliessen.

Bewusst ohne ``asyncio``, Threads, Scheduler oder Nebenlaeufigkeit — Phase 0 ist
sequenziell (ein Test prueft das per AST).

Block-Erkennung (docs/04 §Vorpruefung, README §Compliance): eine Consent-Wall,
ein CAPTCHA oder eine Challenge wird **an der Signatur des Koerpers** erkannt,
nie am Statuscode. Auf einen Treffer bricht der Runner die **ganze Domain** ab
(kein Extraktor, die Discovery-Schleife endet). Der Ausgang wird ab Phase 1 aus
der Klassifikation abgeleitet (I1.4.6): die Blockade wird ein
``PrecheckVerdict.blocked``, der Klassifikator liefert ``no_action`` und einen
``blocked``-Incident, und der Lauf schliesst **degraded**, nicht failed —
``failed`` bleibt der unbehandelten Ausnahme, dem fehlenden Pack und der zu
niedrigen Zeilenzahl bei ``ok``-Verdikt vorbehalten (docs/04 verlangt fuer die
Negativfaelle degraded/no_action, nie failed).

Vorpruefung -> ``classify`` -> Incident-Writer laufen beim Lauf-Abschluss in
**derselben Transaktion** wie das ``run``-Update; ein Fehler dazwischen rollt
Incidents und ``run.metrics`` gemeinsam zurueck. Der Heiler bleibt abwesend: der
Runner bindet nur ``heal_protocol`` ein, nie ein ``kintsugi.heal.*``-Modul.

``dry_run`` schreibt weiterhin Snapshots (Snapshot-vor-Parsing ist nicht
verhandelbar), aber keine ``record``-Zeile und keinen Incident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from selectolax.lexbor import LexborHTMLParser
from sqlalchemy import func, insert, select, update

from kintsugi.canonical import encode_natural_key
from kintsugi.classify.enums import PrecheckVerdict
from kintsugi.classify.outcome import classify
from kintsugi.classify.precheck import evaluate_precheck
from kintsugi.config import Settings, get_settings
from kintsugi.discovery import DiscoveryContext, get_strategy
from kintsugi.extract.entity import extract_entity
from kintsugi.fetch.block_detect import (
    SignatureHit,
    detect_block,
    detect_soft_404,
    resolve_block_signatures,
    resolve_soft_404_signatures,
)
from kintsugi.fetch.http import HttpFetcher, resolve_encoding
from kintsugi.fetch.ratelimit import DomainLimiter
from kintsugi.fetch.robots import RobotsDenied
from kintsugi.heal_protocol import HealerCapabilities
from kintsugi.packs.model import SitePack
from kintsugi.quality.counters import RunCounters
from kintsugi.quality.history import load_history
from kintsugi.quality.metrics import FetchStats, compute_profile
from kintsugi.quality.profile import QualityProfile
from kintsugi.storage.db import get_engine
from kintsugi.storage.incidents import report
from kintsugi.storage.records import RecordRow, touch_last_seen, write_records
from kintsugi.storage.snapshot_repo import save_snapshot
from kintsugi.storage.snapshots import FilesystemSnapshotStore
from kintsugi.storage.tables import record, site_pack, snapshot
from kintsugi.storage.tables import run as run_table
from kintsugi.validate.dynamic_model import validate_row

if TYPE_CHECKING:
    from typing import Any

    from sqlalchemy import Connection, Row

    from kintsugi.classify.outcome import Signal
    from kintsugi.classify.precheck import PrecheckResult
    from kintsugi.fetch.base import Fetcher

__all__ = ["NoActivePackError", "RunResult", "load_profile", "run"]


class NoActivePackError(Exception):
    """Fuer (domain, entity) ist kein Pack aktiv — es gibt nichts zu laufen."""


@dataclass(frozen=True)
class RunResult:
    """Ergebnis eines Laufs fuer die CLI."""

    run_id: UUID
    status: str
    counters: RunCounters
    error: str | None
    profile: QualityProfile | None = None


def _json_safe(payload: dict[str, object]) -> dict[str, object]:
    """Macht den Payload jsonb-tauglich: Decimal und datetime -> String.

    payload_hash wird ueber genau diese Darstellung gebildet (kanonisch,
    deterministisch); ``gold_book`` castet ``payload->>'price'`` wieder zu numeric.

    Abwesende (``None``) Felder werden **weggelassen**, nie als ``null``
    geschrieben (ADR-009 Kontrakt 1): ein kaputter Selektor ist ein Fill-Rate-
    Einbruch, kein null-Wert im Bestand — ``canonical_json`` lehnt ``None``
    genau darum ab.
    """
    out: dict[str, object] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif isinstance(value, (str, int, bool)):
            out[key] = value
        else:
            out[key] = str(value)  # Decimal u. Ae.
    return out


def load_profile(conn: Connection, run_id: UUID) -> QualityProfile:
    """Liest den Qualitaetsblock strikt: ``ValidationError`` propagiert.

    Ein gespeichertes Dokument, das nicht mehr zum Modell passt, ist ein
    Schema-Drift-Bug und muss laut sein, nie stillschweigend zu ``{}`` gecoerct.
    """
    doc = conn.execute(select(run_table.c.metrics).where(run_table.c.id == run_id)).scalar_one()
    return QualityProfile.model_validate(doc["quality"])


def _resolve_active_pack(conn: Connection, domain: str, entity: str | None) -> list[Row[Any]]:
    stmt = select(site_pack.c.id, site_pack.c.entity, site_pack.c.spec).where(
        site_pack.c.domain == domain, site_pack.c.status == "active"
    )
    if entity is not None:
        stmt = stmt.where(site_pack.c.entity == entity)
    return list(conn.execute(stmt).all())


def run(
    domain: str,
    *,
    entity: str | None = None,
    limit: int | None = None,
    max_urls: int | None = None,
    dry_run: bool = False,
    trigger: str = "manual",
    fetcher: Fetcher | None = None,
    settings: Settings | None = None,
) -> RunResult:
    settings = settings or get_settings()
    engine = get_engine(settings)
    cap = min(x for x in (limit, max_urls) if x is not None) if (limit or max_urls) else None

    with engine.connect() as conn:
        rows_found = _resolve_active_pack(conn, domain, entity)
        if not rows_found:
            raise NoActivePackError(f"kein aktives Pack fuer {domain!r} (entity={entity!r})")
        if len(rows_found) > 1:
            raise NoActivePackError(f"{domain!r} hat mehrere aktive Packs — --entity noetig")
        pack_id, pack_entity, spec = rows_found[0]
        pack = SitePack.model_validate(spec)

        run_id: UUID = conn.execute(
            insert(run_table)
            .values(site_pack_id=pack_id, trigger=trigger, status="running")
            .returning(run_table.c.id)
        ).scalar_one()
        conn.commit()

        counters = RunCounters()
        store = FilesystemSnapshotStore(settings.snapshot_root)
        owns_fetcher = fetcher is None
        if fetcher is None:
            fetcher = HttpFetcher(
                settings,
                limiter=DomainLimiter(pack.fetch.rate_limit_rps, pack.fetch.concurrency),
                respect_robots=pack.fetch.respect_robots is True,
            )

        collected: list[RecordRow] = []
        accepted_payloads: list[dict[str, object]] = []  # typisiert, fuer compute_profile
        unchanged_urls: list[str] = []
        partial_failures = 0
        error: str | None = None
        block_hit: SignatureHit | None = None
        block_evidence: dict[str, object] = {}
        soft_404_hits: list[SignatureHit] = []
        soft_evidence: dict[str, object] = {}

        try:
            ctx = DiscoveryContext(fetcher=fetcher, run_id=run_id, counters=counters)
            strategy = get_strategy(pack.discovery.strategy)
            for index, url in enumerate(strategy.discover(pack, ctx)):
                if cap is not None and index >= cap:
                    break
                counters.rows_considered += 1
                try:
                    snap = save_snapshot(
                        conn,
                        run_id=run_id,
                        url=url,
                        fetcher=fetcher,
                        store=store,
                        site_pack_id=pack_id,
                        entity=pack_entity,
                        trigger=trigger,
                        conditional_requests=pack.fetch.conditional_requests,
                    )
                except RobotsDenied:
                    counters.skip_robots()
                    continue

                if snap.snapshot_id is None:
                    partial_failures += 1  # kein HTTP-Response (in run.error vermerkt)
                    continue
                assert snap.http_status is not None  # gesetzt, sobald eine Zeile existiert
                counters.record_http(snap.http_status)

                if snap.unchanged:
                    counters.rows_unchanged += 1
                    unchanged_urls.append(url)
                    continue
                # 200 und 304 tragen einen extrahierbaren Body (304 aus dem Store,
                # wenn er nicht versionsbewusst unchanged war). 404/5xx nicht.
                if snap.http_status not in (200, 304):
                    partial_failures += 1
                    continue

                # Block-Erkennung NACH dem Snapshot, VOR dem Extraktor.
                assert snap.body is not None
                encoding = resolve_encoding(snap.body, None)
                html = snap.body.decode(encoding, errors="replace")
                block_hit = detect_block(
                    html, {}, resolve_block_signatures(pack.fetch.block_signatures)
                )
                if block_hit is not None:
                    # Eine Blockade bricht die Domain ab: nicht extrahieren, Schleife
                    # beenden. Der Ausgang (degraded + blocked-Incident) faellt beim
                    # Lauf-Abschluss aus der Klassifikation.
                    block_evidence = {
                        "url": url,
                        "http_status": snap.http_status,
                        "snapshot_id": str(snap.snapshot_id),
                    }
                    break

                # Soft-404 (F1: nur Status 200; ein echter HTTP 404 ist kein Soft-404
                # und erreicht diesen Zweig nie — er faellt oben in partial_failures).
                soft_hit = detect_soft_404(
                    html,
                    snap.http_status,
                    url,
                    resolve_soft_404_signatures(pack.fetch.soft_404_signatures),
                )
                if soft_hit is not None:
                    if not soft_404_hits:
                        soft_evidence = {
                            "url": url,
                            "http_status": snap.http_status,
                            "snapshot_id": str(snap.snapshot_id),
                        }
                    soft_404_hits.append(soft_hit)
                    partial_failures += 1
                    continue

                doc = LexborHTMLParser(html)
                values, _ = extract_entity(pack, doc)
                counters.rows_extracted += 1

                result = validate_row(pack, values)
                if not result.accepted:
                    for code in result.reasons:
                        counters.reject(code)
                    continue
                counters.rows_valid += 1
                assert result.payload is not None
                accepted_payloads.append(result.payload)
                natural_key = encode_natural_key(pack.schema_.natural_key, result.payload)
                quality: dict[str, object] | None = (
                    {"violations": result.reasons} if result.reasons else None
                )
                collected.append(
                    RecordRow(
                        natural_key=natural_key,
                        payload=_json_safe(result.payload),
                        snapshot_id=snap.snapshot_id,
                        quality=quality,
                    )
                )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            if owns_fetcher and isinstance(fetcher, HttpFetcher):
                fetcher.close()

        valid_from = datetime.now(UTC)
        duplicates = 0
        if block_hit is None and error is None and not dry_run:
            counters_write = write_records(
                conn,
                entity=pack_entity,
                rows=collected,
                site_pack_id=pack_id,
                run_id=run_id,
                valid_from=valid_from,
            )
            counters.rows_inserted = counters_write.inserted
            counters.rows_versioned = counters_write.versioned
            # counters_write.unchanged (extrahiert, aber payload-identisch beim
            # Schreiben) wird NICHT in rows_unchanged gefaltet: diese Zeilen zaehlen
            # schon in rows_valid, ein Fold wuerde sie in meets_min doppelt zaehlen
            # (rows_valid + rows_unchanged) und min_rows_per_run bei halber
            # Distinktzahl bestehen lassen. rows_unchanged bleibt der dokumentierten
            # Invariante treu: nur die versionsbewusst kurzgeschlossenen Seiten. Der
            # Wert ist aus rows_valid - rows_inserted - rows_versioned ableitbar.
            duplicates = counters_write.duplicates
            if unchanged_urls:
                keys = _natural_keys_for_urls(conn, pack_entity, unchanged_urls)
                touch_last_seen(
                    conn, entity=pack_entity, natural_keys=keys, run_id=run_id, seen_at=valid_from
                )
            # Kein Commit hier: die Records und der Lauf-Abschluss (metrics/status)
            # committen zusammen (#86). Ein Fehler dazwischen rollt beide zurueck.

        # Qualitaetsprofil (I1.1.4): reine Funktion ueber die betrachteten Seiten,
        # der 14-Tage-Median als Baseline. Vorlaeufige Laeufe (running) sind aus
        # load_history ausgeschlossen, dieser Lauf faellt also nicht in seine
        # eigene Baseline.
        history = load_history(conn, domain, pack_entity, valid_from)
        fetch_stats = FetchStats(
            rows_considered=counters.rows_considered,
            # JSON-Objektschluessel sind Strings; HTTP-Status als String (wie to_metrics).
            http={str(status_code): count for status_code, count in counters.http.items()},
            fetch_ms_p95=int(counters.fetch_ms_p95),
            duplicates=duplicates,
            natural_key_missing=counters.rows_rejected.get("natural_key_missing", 0),
            # Versionsbewusst unveraenderte Seiten aus dem Fill-Rate-Nenner nehmen.
            rows_unchanged=len(unchanged_urls),
        )
        profile = compute_profile(accepted_payloads, pack, history, fetch_stats)

        # Vorpruefung -> Klassifikation -> Incidents (I1.4.6), in derselben
        # Transaktion wie der Lauf-Abschluss. Die Vorpruefung liest die
        # Fetch-Ausgaenge (Block-/Soft-404-Treffer, 429/403-Anteil, keine Antwort)
        # und das Kontingent (Phase 1: immer 0). Ein Soft-404 wird nur dann zum
        # Lauf-Verdikt, wenn er der Ausgang war (kein einziger gueltiger Record).
        soft_404_hit = soft_404_hits[0] if (soft_404_hits and counters.rows_valid == 0) else None
        if block_hit is not None:
            fetch_evidence: dict[str, object] = block_evidence
        elif soft_404_hit is not None:
            fetch_evidence = soft_evidence
        else:
            fetch_evidence = {}
        # rate_limited nur bei *gehaeuften* 429/403 (docs/04): die Mehrheit der
        # tatsaechlich abgerufenen Seiten. Ein einzelnes transientes 403/429 darf
        # das Verdikt nicht kippen — sonst maskiert es einen echten Bruch im selben
        # Lauf (die Vorpruefung unterdrueckt dann alle Profil-Signale).
        throttled = counters.http.get(429, 0) + counters.http.get(403, 0)
        # unreachable nur, wenn ueberhaupt gefetcht werden *durfte* und keine
        # Antwort kam. Ein reiner robots-Deny-Lauf (Seiten gar nicht abgerufen) ist
        # nicht „Quelle down" — die Domain ist erreichbar, nur nicht erlaubt.
        attempted = counters.rows_considered - counters.pages_skipped_robots
        precheck = evaluate_precheck(
            max_auto_versions_per_window=pack.healing.max_auto_versions_per_window,
            unreachable=attempted > 0 and not counters.http,
            block_hit=block_hit,
            rate_limited=counters.pages_fetched > 0 and throttled * 2 > counters.pages_fetched,
            soft_404_hit=soft_404_hit,
            auto_versions_in_window=_auto_versions_in_window(
                conn, domain, pack_entity, pack.healing.window
            ),
            evidence=fetch_evidence,
        )
        classification = classify(profile, precheck, pack, HealerCapabilities.NONE)
        # Ein reiner unchanged-Lauf hat ein degeneriertes Profil (nichts neu
        # extrahiert -> fill_rate 0 fuer alles) und bleibt ok: bei ``ok``-Verdikt
        # und null Extraktionen werden die Profil-Signale unterdrueckt. Die
        # Vorpruefungs-Signale (blocked/soft_404/…) gelten dagegen immer.
        degenerate_profile = precheck.verdict is PrecheckVerdict.ok and counters.rows_extracted == 0
        effective_signals = () if degenerate_profile else classification.signals

        # run.status wird aus der Klassifikation abgeleitet, an genau dieser Stelle.
        if not dry_run:
            for signal in effective_signals:
                report(
                    conn,
                    site_pack_id=pack_id,
                    run_id=run_id,
                    kind=signal.incident_kind,
                    severity=signal.severity,
                    field=signal.field,
                    evidence=_signal_evidence(signal, precheck),
                )

        # dry_run ist ein Smoke-Test und scheitert nicht an min_rows. Ein
        # nicht-``ok``-Verdikt oder ein Warn+-Signal macht degraded, nie failed;
        # failed bleibt der zu niedrigen Zeilenzahl bei ``ok``-Verdikt vorbehalten.
        meets_min = dry_run or counters.meets_min_rows(pack.quality.min_rows_per_run)
        fired = any(sig.severity != "info" for sig in effective_signals)
        status = _final_status(
            has_error=error is not None,
            verdict_non_ok=precheck.verdict is not PrecheckVerdict.ok,
            partial=partial_failures > 0 or fired,
            meets_min=meets_min,
        )
        if status == "failed" and error is None:
            error = (
                f"row_count_below_min: {counters.rows_valid + counters.rows_unchanged} "
                f"< {pack.quality.min_rows_per_run}"
            )

        conn.execute(
            update(run_table)
            .where(run_table.c.id == run_id)
            .values(
                status=status,
                # Server-Uhr, damit finished_at >= started_at (beide DB-seitig)
                # trotz Client/Server-Uhren-Skew gilt (ck_run_finished_after_started).
                finished_at=func.now(),
                error=func.concat_ws("\n", run_table.c.error, error)
                if error
                else run_table.c.error,
                # namespaced (#82): Betriebs- und Qualitaetsblock in einem Dokument.
                metrics={
                    "counters": counters.to_metrics(),
                    "quality": profile.model_dump(mode="json"),
                },
                pages_fetched=counters.pages_fetched,
                rows_extracted=counters.rows_extracted,
            )
        )
        conn.commit()

    return RunResult(run_id=run_id, status=status, counters=counters, error=error, profile=profile)


def _natural_keys_for_urls(conn: Connection, entity: str, urls: list[str]) -> list[str]:
    joined = record.join(snapshot, snapshot.c.id == record.c.snapshot_id)
    result = conn.execute(
        select(record.c.natural_key)
        .select_from(joined)
        .where(
            record.c.entity == entity,
            record.c.valid_to.is_(None),
            snapshot.c.url.in_(urls),
        )
        .distinct()
    ).scalars()
    return list(result)


def _final_status(*, has_error: bool, verdict_non_ok: bool, partial: bool, meets_min: bool) -> str:
    # Genau eine Stelle, an der run.status entsteht (I1.4.6). Reihenfolge ist die
    # Praezedenz: eine unbehandelte Ausnahme ist failed; ein Nicht-``ok``-Verdikt
    # (blocked/unreachable/rate_limited/soft_404/quota) oder ein Teilausfall ist
    # degraded, nie failed; failed bleibt der zu niedrigen Zeilenzahl bei
    # ``ok``-Verdikt ohne Teilausfall vorbehalten (docs/04-Negativfaelle).
    if has_error:
        return "failed"
    if verdict_non_ok:
        return "degraded"
    if partial:
        return "degraded"
    if not meets_min:
        return "failed"
    return "ok"


def _window_days(window: str) -> int:
    """Parst ``healing.window`` (z. B. ``"7d"``) zu Tagen; Fallback 7."""
    if window.endswith("d"):
        try:
            return int(window[:-1])
        except ValueError:
            return 7
    return 7


def _auto_versions_in_window(conn: Connection, domain: str, entity: str, window: str) -> int:
    """Zahl der vom Heiler erzeugten Pack-Versionen im Fenster (Phase 1: immer 0).

    ``created_by LIKE 'healer:%'`` unterscheidet Maschinen- von Menschenversionen
    (docs/02 §Lebenszyklus). Der Zeitfilter nutzt die Server-Uhr (``now()``), nie
    die Client-Uhr — sonst flackert die Grenze bei Uhren-Skew.
    """
    count: int = conn.execute(
        select(func.count())
        .select_from(site_pack)
        .where(
            site_pack.c.domain == domain,
            site_pack.c.entity == entity,
            site_pack.c.created_by.like("healer:%"),
            site_pack.c.created_at
            >= func.now() - func.make_interval(0, 0, 0, _window_days(window)),
        )
    ).scalar_one()
    return count


def _signal_evidence(signal: Signal, precheck: PrecheckResult) -> dict[str, object]:
    """Baut das Evidence-Dict eines Signal-Incidents (#98) am Aufrufort zusammen."""
    evidence: dict[str, object] = {
        "signal": signal.id,
        "field": signal.field,
        "observed": signal.observed,
        "threshold": signal.threshold,
    }
    # Fuer Verdikt-Signale (blocked/unreachable/…) traegt die Vorpruefung
    # matched_signature, url, http_status und snapshot_id bei.
    evidence.update(precheck.evidence)
    return evidence
