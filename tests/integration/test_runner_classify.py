"""Runner-Verdrahtung: Vorpruefung, classify und Incident-Writer (I1.4.6)."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from alembic import command
from alembic.config import Config
from kintsugi.config import Settings
from kintsugi.fetch.base import FetchOutcome, FetchResult
from kintsugi.fetch.http import HttpFetcher
from kintsugi.fetch.ratelimit import DomainLimiter
from kintsugi.packs.loader import load_pack
from kintsugi.quality.run_metrics import RunMetrics
from kintsugi.runner import run
from kintsugi.storage.db import get_engine
from kintsugi.storage.tables import incident, record, site_pack
from kintsugi.storage.tables import run as run_table
from sqlalchemy import Connection, func, insert, select, text

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTACT = "kintsugi-bot (+mailto:ops@example.com)"
CONSENT_BODY = (
    b'<html><body><div id="onetrust-consent-sdk">We value your privacy</div>'
    b"<p>Bitte stimmen Sie zu.</p></body></html>"
)


@pytest.fixture(scope="module")
def engine():
    eng = get_engine()
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"kein postgres erreichbar: {exc}")
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    return eng


@pytest.fixture
def conn(engine):
    with engine.connect() as connection:
        connection.execute(text("TRUNCATE site_pack CASCADE"))
        connection.commit()
        yield connection


def _settings(tmp_path) -> Settings:
    return Settings(contact=CONTACT, snapshot_root=tmp_path / "bronze")


def _fast_fetcher() -> HttpFetcher:
    return HttpFetcher(
        Settings(contact=CONTACT), limiter=DomainLimiter(2000.0, 4), respect_robots=True
    )


def _activate_pack(
    conn: Connection,
    base: str,
    *,
    broken_field: str | None = None,
    min_rows: int = 5,
    conditional: bool = True,
) -> UUID:
    pack = load_pack("books.toscrape.com", "book", root=Path("packs"))
    disc = pack.discovery.model_copy(
        update={
            "url_template": f"{base}/catalogue/page-{{n}}.html",
            "url_pattern": r"^http://127\.0\.0\.1:\d+/catalogue/[^/]+/index\.html$",
        }
    )
    updates: dict[str, object] = {
        "discovery": disc,
        "fetch": pack.fetch.model_copy(
            update={"rate_limit_rps": 2000.0, "conditional_requests": conditional}
        ),
        "quality": pack.quality.model_copy(update={"min_rows_per_run": min_rows}),
    }
    if broken_field is not None:
        source = pack.extract.sources[0]
        fields = dict(source.fields)  # type: ignore[attr-defined]
        fields[broken_field] = fields[broken_field].model_copy(
            update={"selector": ".kintsugi-broken-selector-xyz"}
        )
        updates["extract"] = pack.extract.model_copy(
            update={"sources": [source.model_copy(update={"fields": fields})]}
        )
    pack = pack.model_copy(update=updates)
    spec = json.loads(pack.model_dump_json(by_alias=True))
    pack_id: UUID = conn.execute(
        insert(site_pack)
        .values(
            domain="books.toscrape.com",
            entity="book",
            version=1,
            spec=spec,
            created_by="human:test",
            status="active",
            activated_at=func.now(),
        )
        .returning(site_pack.c.id)
    ).scalar_one()
    conn.commit()  # die separate run()-Connection muss das aktive Pack sehen
    return pack_id


def _count(conn: Connection, table) -> int:
    return conn.execute(select(func.count()).select_from(table)).scalar_one()


def _open_incidents(conn: Connection) -> list[tuple[str, str | None, str]]:
    return conn.execute(
        text("select kind, field, severity from incident where closed_at is null order by kind")
    ).all()  # type: ignore[return-value]


class WrapFetcher:
    """Delegiert, faelscht aber Detailseiten nach ``mode`` (block | outage)."""

    def __init__(self, inner: HttpFetcher, *, mode: str) -> None:
        self.inner = inner
        self.mode = mode
        self._seen = 0

    def _is_detail(self, url: str) -> bool:
        return url.endswith("/index.html") and "page-" not in url

    def fetch(self, url, *, etag=None, last_modified=None):
        if self._is_detail(url):
            if self.mode == "block":
                return FetchResult(
                    url=url,
                    final_url=url,
                    http_status=200,
                    headers={},
                    body=CONSENT_BODY,
                    content_type="text/html",
                    encoding="utf-8",
                    elapsed_ms=1,
                    fetcher="httpx",
                    from_cache=False,
                    outcome=FetchOutcome.ok,
                )
            if self.mode == "outage":
                self._seen += 1
                if self._seen % 3 == 0:  # rund 30 % der Seiten fallen aus
                    raise httpx.ConnectError("injizierter Ausfall")
            if self.mode == "one_403":
                self._seen += 1
                if self._seen == 1:  # genau eine transiente Drossel
                    inner = self.inner.fetch(url, etag=etag, last_modified=last_modified)
                    return replace(inner, http_status=403, outcome=FetchOutcome.rate_limited)
        return self.inner.fetch(url, etag=etag, last_modified=last_modified)


class MutateFetcher:
    """Aendert die ersten ``mutate_first`` Detailseiten (content_hash) ohne die
    extrahierten Werte zu beruehren — der Rest bleibt byte-identisch."""

    def __init__(self, inner: HttpFetcher, *, mutate_first: int) -> None:
        self.inner = inner
        self.mutate_first = mutate_first
        self._detail_seen = 0

    def _is_detail(self, url: str) -> bool:
        return url.endswith("/index.html") and "page-" not in url

    def fetch(self, url, *, etag=None, last_modified=None):
        result = self.inner.fetch(url, etag=etag, last_modified=last_modified)
        if self._is_detail(url) and result.body:
            self._detail_seen += 1
            if self._detail_seen <= self.mutate_first:
                body = result.body.replace(b"</body>", b"<!-- mutiert --></body>", 1)
                return replace(result, body=body)
        return result


def test_kaputter_selektor_ist_degraded_mit_einem_fill_rate_incident(
    conn, books_fixture_base_url, tmp_path
):
    pack_id = _activate_pack(conn, books_fixture_base_url, broken_field="availability")
    result = run(
        "books.toscrape.com",
        fetcher=_fast_fetcher(),
        settings=_settings(tmp_path),
        max_urls=15,
    )
    assert result.status == "degraded"
    assert _open_incidents(conn) == [("fill_rate_drop", "availability", "warn")]
    # run.metrics rundet als das vereinbarte Metrik-Modell (namespaced).
    metrics = conn.execute(
        select(run_table.c.metrics).where(run_table.c.site_pack_id == pack_id)
    ).scalar_one()
    RunMetrics.model_validate(metrics)


def test_blocked_ein_incident_null_fill_rate_degraded(conn, books_fixture_base_url, tmp_path):
    _activate_pack(conn, books_fixture_base_url)
    result = run(
        "books.toscrape.com",
        fetcher=WrapFetcher(_fast_fetcher(), mode="block"),
        settings=_settings(tmp_path),
    )
    assert result.status == "degraded"
    incidents = _open_incidents(conn)
    assert incidents == [("blocked", None, "warn")]  # genau ein blocked, kein fill_rate_drop
    assert _count(conn, record) == 0


def test_dreissig_prozent_leer_ist_degraded_nicht_failed(conn, books_fixture_base_url, tmp_path):
    _activate_pack(conn, books_fixture_base_url, min_rows=5)
    result = run(
        "books.toscrape.com",
        fetcher=WrapFetcher(_fast_fetcher(), mode="outage"),
        settings=_settings(tmp_path),
        max_urls=15,
    )
    assert result.status == "degraded"


def test_zu_wenige_betrachtete_zeilen_bei_ok_ist_failed(conn, books_fixture_base_url, tmp_path):
    _activate_pack(conn, books_fixture_base_url, min_rows=200)
    result = run(
        "books.toscrape.com",
        fetcher=_fast_fetcher(),
        settings=_settings(tmp_path),
        max_urls=5,
    )
    assert result.status == "failed"
    assert _open_incidents(conn) == []


def test_sauberer_lauf_null_incidents_und_ok(conn, books_fixture_base_url, tmp_path):
    _activate_pack(conn, books_fixture_base_url, min_rows=5)
    result = run(
        "books.toscrape.com",
        fetcher=_fast_fetcher(),
        settings=_settings(tmp_path),
        max_urls=15,
    )
    assert result.status == "ok"
    assert _open_incidents(conn) == []


def test_exception_nach_report_rollt_incident_und_metrics_zurueck(
    conn, books_fixture_base_url, tmp_path, monkeypatch
):
    import kintsugi.runner as runner_mod

    real_report = runner_mod.report

    def boom(*args, **kwargs):
        real_report(*args, **kwargs)  # der Incident wird in die Transaktion geschrieben
        raise RuntimeError("injizierter Fehler nach report()")

    monkeypatch.setattr(runner_mod, "report", boom)

    _activate_pack(conn, books_fixture_base_url, broken_field="availability")
    with pytest.raises(RuntimeError, match="nach report"):
        run(
            "books.toscrape.com",
            fetcher=_fast_fetcher(),
            settings=_settings(tmp_path),
            max_urls=15,
        )
    # Beides rollt zurueck: kein Incident, und run.metrics blieb der leere
    # running-Default (der counters/quality-Block wurde nie committet).
    assert _count(conn, incident) == 0
    metrics = conn.execute(select(run_table.c.metrics)).scalar_one()
    assert "quality" not in metrics
    assert "counters" not in metrics


def test_rerun_dedupliziert_auf_occurrences_zwei(conn, books_fixture_base_url, tmp_path):
    _activate_pack(conn, books_fixture_base_url, broken_field="availability")
    run(
        "books.toscrape.com",
        fetcher=_fast_fetcher(),
        settings=_settings(tmp_path),
        max_urls=15,
    )
    # Zweiter Lauf als replay: umgeht den versionsbewussten unchanged-Kurzschluss,
    # behaelt aber die site_pack_id -> derselbe Incident wird dedupliziert.
    run(
        "books.toscrape.com",
        fetcher=_fast_fetcher(),
        settings=_settings(tmp_path),
        max_urls=15,
        trigger="replay",
    )
    open_count = conn.execute(
        text("select count(*) from incident where closed_at is null")
    ).scalar_one()
    assert open_count == 1
    occ = conn.execute(
        text("select evidence->>'occurrences' from incident where closed_at is null")
    ).scalar_one()
    assert occ == "2"


def test_inkrementeller_lauf_mit_wenigen_aenderungen_bleibt_ok(
    conn, books_fixture_base_url, tmp_path
):
    # Review-Fix: der Fill-Rate-Nenner darf die versionsbewusst unveraenderten
    # Seiten nicht mitzaehlen — sonst faerbt jeder inkrementelle Lauf (die meisten
    # Seiten unveraendert, wenige neu) die Fill-Rate faelschlich auf fast 0 und
    # oeffnet Fehlalarm-Incidents.
    _activate_pack(conn, books_fixture_base_url, min_rows=5, conditional=False)
    run("books.toscrape.com", fetcher=_fast_fetcher(), settings=_settings(tmp_path), max_urls=15)
    # Zweiter Lauf: 2 Seiten aendern sich (neu extrahiert, Werte identisch), 13
    # sind versionsbewusst unveraendert und werden vor der Extraktion kurzgeschlossen.
    result = run(
        "books.toscrape.com",
        fetcher=MutateFetcher(_fast_fetcher(), mutate_first=2),
        settings=_settings(tmp_path),
        max_urls=15,
    )
    assert result.status == "ok"
    assert _open_incidents(conn) == []


def test_einzelnes_403_maskiert_keinen_bruch(conn, books_fixture_base_url, tmp_path):
    # Review-Fix: ein einzelnes transientes 403 darf das Verdikt nicht auf
    # rate_limited kippen (das alle Profil-Signale unterdruecken wuerde). Der
    # echte Selektor-Bruch muss trotzdem als fill_rate_drop gemeldet werden.
    _activate_pack(conn, books_fixture_base_url, broken_field="title", min_rows=5)
    result = run(
        "books.toscrape.com",
        fetcher=WrapFetcher(_fast_fetcher(), mode="one_403"),
        settings=_settings(tmp_path),
        max_urls=15,
    )
    assert result.status == "degraded"
    incidents = _open_incidents(conn)
    assert ("fill_rate_drop", "title") in {(k, f) for k, f, _sev in incidents}
    assert not any(k == "rate_limited" for k, _f, _sev in incidents)


def test_write_unchanged_zaehlt_nicht_doppelt_gegen_min_rows(
    conn, books_fixture_base_url, tmp_path
):
    # Review-Fix: write-time-unchanged Zeilen (payload-identisch beim Schreiben)
    # stehen schon in rows_valid und duerfen nicht zusaetzlich in rows_unchanged
    # gefaltet werden — sonst besteht min_rows_per_run bei halber Distinktzahl.
    _activate_pack(conn, books_fixture_base_url, min_rows=20, conditional=False)
    run("books.toscrape.com", fetcher=_fast_fetcher(), settings=_settings(tmp_path), max_urls=15)
    # Zweiter Lauf: alle 15 Seiten aendern ihren Rohkoerper (content_hash), aber der
    # extrahierte payload bleibt identisch -> 15 write-unchanged, 0 Kurzschluss.
    result = run(
        "books.toscrape.com",
        fetcher=MutateFetcher(_fast_fetcher(), mutate_first=15),
        settings=_settings(tmp_path),
        max_urls=15,
    )
    # 15 distinkte Records < min_rows 20 -> failed (nicht faelschlich ok durch 15+15).
    assert result.status == "failed"


def test_runner_bindet_kein_heal_modul():
    assert not (PROJECT_ROOT / "kintsugi" / "heal").exists()
    code = (
        "import sys; import kintsugi.runner; "
        "bad=[m for m in sys.modules if m=='kintsugi.heal' or m.startswith('kintsugi.heal.')]; "
        "assert not bad, bad; print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
