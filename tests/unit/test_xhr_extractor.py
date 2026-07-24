"""XHR-Extraktor: Zeilen-/Feld-Mapping, Template, typisierte Misses, Fetcher-Naht (I1.5.3).

Alle Faelle laufen gegen eine **Attrappe** des Fetchers -- kein echtes Netz. Der
Extraktor darf nur ueber den Fetcher ins Netz (nie ``httpx`` direkt), damit das
robots-Gate und die Zulassungspruefung (I1.5.1) den einzigen HTTP-Pfad bleiben.
"""

from __future__ import annotations

import json
from pathlib import Path

from kintsugi.extract.xhr import XhrExtractor
from kintsugi.fetch.base import FetchOutcome, FetchResult
from kintsugi.fetch.robots import RobotsDenied
from kintsugi.packs.model import XhrSource


def _result(
    body: str, *, status: int = 200, content_type: str | None = "application/json"
) -> FetchResult:
    return FetchResult(
        url="https://api.example.com/films",
        final_url="https://api.example.com/films",
        http_status=status,
        headers={},
        body=body.encode("utf-8"),
        content_type=content_type,
        encoding="utf-8",
        elapsed_ms=3,
        fetcher="httpx",
        from_cache=False,
        outcome=FetchOutcome.ok if status == 200 else FetchOutcome.not_found,
    )


class _FakeFetcher:
    """Attrappe: zeichnet (url, headers) auf und liefert ein festes FetchResult."""

    def __init__(self, result: FetchResult | None = None, *, deny: bool = False) -> None:
        self._result = result
        self._deny = deny
        self.calls: list[tuple[str, object]] = []

    def fetch(self, url, *, etag=None, last_modified=None, headers=None):
        self.calls.append((url, headers))
        if self._deny:
            raise RobotsDenied(url)  # robots verbietet -> vor jeder Anfrage
        assert self._result is not None
        return self._result


_FILMS = json.dumps(
    [
        {"title": "Spotlight  ", "year": 2015, "awards": {"best_picture": True}},
        {"title": "Mad Max", "year": 2015},
    ]
)


def test_row_root_und_feld_mapping():
    source = XhrSource(
        kind="xhr",
        endpoint="https://api.example.com/films",
        row_root="$[*]",
        fields={"title": "$.title", "year": "$.year", "best_picture": "awards.best_picture"},
    )
    rows = XhrExtractor(_FakeFetcher(_result(_FILMS))).extract_all(
        source, page_url="https://x.com/films/"
    )
    assert len(rows) == 2
    assert rows[0] == {"title": "Spotlight  ", "year": 2015, "best_picture": True}
    # best_picture fehlt auf Zeile 2 -> das Feld faellt weg (Fill-Rate-Signal).
    assert rows[1] == {"title": "Mad Max", "year": 2015}


def test_ohne_fields_map_bleiben_die_rohen_keys():
    source = XhrSource(kind="xhr", endpoint="https://api.example.com/films", row_root="$[*]")
    rows = XhrExtractor(_FakeFetcher(_result(_FILMS))).extract_all(
        source, page_url="https://x.com/"
    )
    assert rows[0]["title"] == "Spotlight  "
    assert rows[0]["awards"] == {"best_picture": True}


def test_transforms_strippen_den_feldwert():
    # Strukturierte Quellen tragen dieselbe per-Feld-Transform-Kette wie css.
    source = XhrSource(
        kind="xhr",
        endpoint="https://api.example.com/films",
        row_root="$[*]",
        fields={"title": "$.title"},
        transforms={"title": ["strip"]},
    )
    rows = XhrExtractor(_FakeFetcher(_result(_FILMS))).extract_all(
        source, page_url="https://x.com/"
    )
    assert rows[0]["title"] == "Spotlight"  # aus "Spotlight  "


def test_url_template_platzhalter_aus_der_seiten_url():
    source = XhrSource(
        kind="xhr", endpoint="https://api.example.com/films?year={year}", row_root="$[*]"
    )
    fetcher = _FakeFetcher(_result("[]"))
    XhrExtractor(fetcher).extract_all(source, page_url="https://x.com/films/?year=2015")
    assert fetcher.calls[0][0] == "https://api.example.com/films?year=2015"


def test_params_werden_angehaengt_und_substituiert():
    source = XhrSource(
        kind="xhr",
        endpoint="https://api.example.com/films",
        params={"ajax": "true", "year": "{year}"},
        row_root="$[*]",
    )
    fetcher = _FakeFetcher(_result("[]"))
    XhrExtractor(fetcher).extract_all(source, page_url="https://x.com/?year=2010")
    url = fetcher.calls[0][0]
    assert url.startswith("https://api.example.com/films?")
    assert "ajax=true" in url
    assert "year=2010" in url


def test_extra_header_werden_dem_fetcher_gereicht():
    source = XhrSource(
        kind="xhr",
        endpoint="https://api.example.com/films",
        headers={"X-Requested-With": "XMLHttpRequest"},
        row_root="$[*]",
    )
    fetcher = _FakeFetcher(_result("[]"))
    XhrExtractor(fetcher).extract_all(source, page_url="https://x.com/")
    assert fetcher.calls[0][1] == {"X-Requested-With": "XMLHttpRequest"}


def test_404_ist_ein_typisierter_miss():
    source = XhrSource(kind="xhr", endpoint="https://api.example.com/films", row_root="$[*]")
    assert (
        XhrExtractor(_FakeFetcher(_result("[]", status=404))).extract_all(
            source, page_url="https://x.com/"
        )
        == []
    )


def test_html_content_type_ist_ein_typisierter_miss():
    source = XhrSource(kind="xhr", endpoint="https://api.example.com/films", row_root="$[*]")
    fetcher = _FakeFetcher(_result("<html></html>", content_type="text/html; charset=utf-8"))
    assert XhrExtractor(fetcher).extract_all(source, page_url="https://x.com/") == []


def test_fetcher_genau_einmal_und_snapshot_vor_dem_parsen():
    captured: list[FetchResult] = []
    source = XhrSource(
        kind="xhr",
        endpoint="https://api.example.com/films",
        row_root="$[*]",
        fields={"t": "$.title"},
    )
    fetcher = _FakeFetcher(_result(_FILMS))
    rows = XhrExtractor(fetcher, snapshot=captured.append).extract_all(
        source, page_url="https://x.com/"
    )
    assert len(fetcher.calls) == 1  # genau ein Fetch
    assert len(captured) == 1  # genau ein Snapshot
    # Der Snapshot bekam die ROHE Antwort (vor dem Parsen): Body == JSON-Bytes,
    # content_type application/json (docs/03 Bronze, replaybar).
    assert captured[0].body == _FILMS.encode("utf-8")
    assert captured[0].content_type == "application/json"
    assert rows[0]["t"] == "Spotlight  "  # danach korrekt geparst


def test_modul_importiert_httpx_nicht_direkt():
    # Der einzige HTTP-Pfad ist der Fetcher; ein blankes httpx umginge robots.
    src = Path("kintsugi/extract/xhr.py").read_text(encoding="utf-8")
    assert "import httpx" not in src


def test_robots_deny_liefert_null_requests_und_miss():
    source = XhrSource(kind="xhr", endpoint="https://api.example.com/films", row_root="$[*]")
    captured: list[FetchResult] = []
    result = XhrExtractor(_FakeFetcher(deny=True), snapshot=captured.append).extract_all(
        source, page_url="https://x.com/"
    )
    assert result == []
    assert captured == []  # kein Snapshot -> es kam keine Antwort (null Requests)
