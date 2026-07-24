"""Replay-Harness: Exact-Match-Gate, Optional-Baseline, kein I/O, Parse-Cache (I1.3.5)."""

from __future__ import annotations

from pathlib import Path

from kintsugi.cli import app
from kintsugi.harness.replay import Corpus, replay
from kintsugi.packs.loader import load_pack
from kintsugi.packs.model import SitePack
from typer.testing import CliRunner

runner = CliRunner()
FIXTURES = Path("fixtures")


def _pack() -> SitePack:
    return load_pack("books.toscrape.com", "book", root=Path("packs"))


def _corpus() -> Corpus:
    return Corpus(FIXTURES, "books.toscrape.com", "book")


def _mutate_selector(field: str, selector: str) -> SitePack:
    data = _pack().model_dump(by_alias=True)
    data["extract"]["sources"][0]["fields"][field]["selector"] = selector
    return SitePack.model_validate(data)


def test_committeter_corpus_besteht():
    assert replay(_pack(), _corpus()).passed is True


def test_cli_exit0_und_mutierter_selektor_exit1():
    result = runner.invoke(app, ["replay", "books.toscrape.com"])
    assert result.exit_code == 0, result.output
    # Ein gebrochener Selektor -> Pflichtwert weicht ab -> Durchfall.
    broken = _mutate_selector("title", "div.gibt-es-nicht")
    assert replay(broken, _corpus()).passed is False


def test_ein_abweichender_pflichtwert_reisst_den_report():
    report = replay(_mutate_selector("upc", "td.gibt-es-nicht"), _corpus())
    assert report.passed is False
    # Genau die upc-Felder sind nicht ok.
    bad = [
        fr
        for fixture in report.fixtures
        for fr in fixture.fields
        if fr.field == "upc" and not fr.ok
    ]
    assert bad


def test_optionales_feld_unter_baseline_faellt_durch():
    corpus = _corpus()
    # availability-Selektor brechen -> alle availability None -> unter Baseline.
    report = replay(_mutate_selector("availability", "p.gibt-es-nicht"), corpus)
    assert report.passed is False
    assert report.optional_counts["availability"] < report.baseline["availability"]


def test_replay_macht_kein_io(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("replay darf kein Netz/keine DB anfassen")

    monkeypatch.setattr("httpx.Client.request", _boom)
    monkeypatch.setattr("kintsugi.storage.db.get_engine", _boom)
    assert replay(_pack(), _corpus()).passed is True


def _quotes_pack() -> SitePack:
    return load_pack("quotes.toscrape.com", "quote", root=Path("packs"))


def _quotes_corpus() -> Corpus:
    return Corpus(FIXTURES, "quotes.toscrape.com", "quote")


def test_quotes_mehrzeilen_corpus_besteht():
    # #106: der mehrzeilige quotes-Corpus (N Zitate je /js/-Seite) besteht das Gate.
    assert replay(_quotes_pack(), _quotes_corpus()).passed is True


def test_quotes_corpus_ist_wirklich_mehrzeilig():
    fixtures = _quotes_corpus().fixtures()
    assert len(fixtures) == 30
    assert any(f.expected.expected_row_count > 1 for f in fixtures)  # N>1 wird geprueft
    assert any(f.expected.expected_row_count == 0 for f in fixtures)  # leere Seite dabei


def test_quotes_gebrochener_embedded_json_pfad_reisst_report():
    data = _quotes_pack().model_dump(by_alias=True)
    data["extract"]["sources"][0]["fields"]["text"] = "$.gibtsnicht"
    broken = SitePack.model_validate(data)
    assert replay(broken, _quotes_corpus()).passed is False


def test_jede_fixture_wird_genau_einmal_geparst():
    corpus = _corpus()
    replay(_pack(), corpus)
    first = corpus.parse_count
    assert first == len(corpus.fixtures())  # eine Fixture, ein Parse
    replay(_pack(), corpus)  # zweiter Lauf nutzt den Cache
    assert corpus.parse_count == first
