"""Die Phase-0 Definition of Done als ausfuehrbarer Test (I0.9.10).

docs/08-roadmap.md §Phase 0, DoD: „``uv run kintsugi run books.toscrape.com``
schreibt mindestens 200 validierte Records nach Postgres, jeder mit Verweis auf
Snapshot und Site-Pack-Version. Zweiter Lauf schreibt keine Duplikate."

Der Standardlauf ist offline gegen den Fixture-Server (0.5 rps ueber ~1050 URLs
waeren ~35 Minuten, docs/07). Die ``live``-Variante faehrt dieselben Zusagen mit
kleinem ``--max-urls`` gegen die echte Seite; die addopts (``not live``) halten CI
offline. F1 zaehlt end to end: ohne die 404-heisst-allow-Regel gaebe es null
Records.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from alembic import command
from alembic.config import Config
from kintsugi.config import Settings, get_settings
from kintsugi.fetch.http import HttpFetcher
from kintsugi.fetch.ratelimit import DomainLimiter
from kintsugi.packs.loader import load_pack
from kintsugi.runner import run
from kintsugi.storage.db import get_engine
from kintsugi.storage.tables import record, site_pack, snapshot
from sqlalchemy import Connection, func, insert, select, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTACT = "kintsugi-bot (+mailto:ops@example.com)"


# --- CI-Verdrahtung (kein Postgres noetig, laeuft im Standardlauf) -------------


def test_ci_hat_genau_einen_postgres_job_mit_dod_step():
    ci = yaml.safe_load((PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    with_pg = {
        name: job for name, job in ci["jobs"].items() if "postgres" in (job.get("services") or {})
    }
    assert len(with_pg) == 1, f"genau ein Job mit services.postgres erwartet, nicht {list(with_pg)}"
    job = next(iter(with_pg.values()))
    assert job["services"]["postgres"]["image"] == "postgres:16"
    steps = " ".join(str(step.get("run", "")) for step in job["steps"])
    assert "test_phase0_dod.py" in steps  # der DoD-Test laeuft als Schritt


# --- Der DoD offline -----------------------------------------------------------


def _migrated_engine():
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    return eng


def _activate_books_pack(conn: Connection, base: str) -> None:
    pack = load_pack("books.toscrape.com", "book", root=Path("packs"))
    disc = pack.discovery.model_copy(
        update={
            "url_template": f"{base}/catalogue/page-{{n}}.html",
            "url_pattern": r"^http://127\.0\.0\.1:\d+/catalogue/[^/]+/index\.html$",
        }
    )
    fetch = pack.fetch.model_copy(update={"rate_limit_rps": 2000.0})
    pack = pack.model_copy(update={"discovery": disc, "fetch": fetch})
    spec = json.loads(pack.model_dump_json(by_alias=True))
    conn.execute(
        insert(site_pack).values(
            domain="books.toscrape.com",
            entity="book",
            version=1,
            spec=spec,
            created_by="human:test",
            status="active",
            activated_at=func.now(),
        )
    )
    conn.commit()


def _counts(conn: Connection) -> dict[str, int]:
    rec = conn.execute(select(func.count()).select_from(record)).scalar_one()
    current = conn.execute(
        select(func.count()).select_from(record).where(record.c.valid_to.is_(None))
    ).scalar_one()
    snap = conn.execute(select(func.count()).select_from(snapshot)).scalar_one()
    return {"record": rec, "current": current, "snapshot": snap}


@pytest.mark.integration
def test_phase0_dod_offline(books_fixture_base_url, tmp_path, monkeypatch):
    engine = _migrated_engine()
    with engine.connect() as conn:
        _activate_books_pack(conn, books_fixture_base_url)
        active_pack_id = conn.execute(select(site_pack.c.id)).scalar_one()

    monkeypatch.setenv("KINTSUGI_CONTACT", CONTACT)
    monkeypatch.setenv("KINTSUGI_SNAPSHOT_ROOT", str(tmp_path / "bronze"))
    get_settings.cache_clear()

    # Lauf 1 — genau der Einstiegspunkt, den die CLI ruft.
    r1 = run("books.toscrape.com")
    assert r1.status == "ok"

    with engine.connect() as conn:
        after1 = _counts(conn)
        # 1. mindestens 200 Records.
        assert after1["record"] >= 200
        # 2. keine Zeile ohne Snapshot- oder Pack-Verweis.
        orphans = conn.execute(
            select(func.count())
            .select_from(record)
            .where(record.c.snapshot_id.is_(None) | record.c.site_pack_id.is_(None))
        ).scalar_one()
        assert orphans == 0
        # 3. Provenance verlustfrei: die Joins liefern so viele Zeilen wie record.
        rec_snap = conn.execute(
            select(func.count()).select_from(
                record.join(snapshot, snapshot.c.id == record.c.snapshot_id)
            )
        ).scalar_one()
        rec_pack = conn.execute(
            select(func.count()).select_from(
                record.join(site_pack, site_pack.c.id == record.c.site_pack_id)
            )
        ).scalar_one()
        assert rec_snap == rec_pack == after1["record"]
        # jede aktuelle Zeile traegt das aktive Pack.
        foreign = conn.execute(
            select(func.count())
            .select_from(record)
            .where(record.c.valid_to.is_(None), record.c.site_pack_id != active_pack_id)
        ).scalar_one()
        assert foreign == 0

    # Lauf 2 — keine Duplikate, aber ein frischer Snapshot je Seite.
    r2 = run("books.toscrape.com")
    assert r2.status == "ok"
    with engine.connect() as conn:
        after2 = _counts(conn)
    assert after2["record"] == after1["record"]
    assert after2["current"] == after1["current"]
    assert after2["snapshot"] > after1["snapshot"]


# --- Der DoD live (standardmaessig abgewaehlt) ---------------------------------


@pytest.mark.live
def test_phase0_dod_live(tmp_path, monkeypatch):
    engine = _migrated_engine()
    with engine.connect() as conn:
        pack = load_pack("books.toscrape.com", "book", root=Path("packs"))
        spec = json.loads(pack.model_dump_json(by_alias=True))
        conn.execute(
            insert(site_pack).values(
                domain="books.toscrape.com",
                entity="book",
                version=1,
                spec=spec,
                created_by="human:test",
                status="active",
                activated_at=func.now(),
            )
        )
        conn.commit()

    settings = Settings(contact=CONTACT, snapshot_root=tmp_path / "bronze")
    fetcher = HttpFetcher(settings, limiter=DomainLimiter(0.5, 2), respect_robots=True)
    result = run("books.toscrape.com", fetcher=fetcher, settings=settings, max_urls=40)
    assert result.counters.rows_valid >= 1
