"""Prueft den Pflicht-Compliance-Block und die strukturierte Robots-Ausnahme (I0.6.4)."""

from __future__ import annotations

import pytest
from kintsugi.packs.model import ComplianceSpec, FetchSpec, SitePack
from pydantic import ValidationError

_COMPLIANCE = {
    "tos_url": "https://books.toscrape.com/",
    "tos_verdict": "permits",
    "tos_reviewed_at": "2026-07-21",
    "reviewed_by": "human:sven",
    "robots_checked_at": "2026-07-21",
    "public_content": True,
    "personal_data": False,
}


def _pack(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "apiVersion": "kintsugi/v1",
        "domain": "books.toscrape.com",
        "entity": "book",
        "version": 1,
        "discovery": {"strategy": "pagination", "url_template": "p-{n}.html"},
        "extract": {"sources": [{"kind": "css", "fields": {"title": {"selector": "h1"}}}]},
        "schema": {"natural_key": ["upc"], "fields": {"upc": {"type": "string", "required": True}}},
        "compliance": dict(_COMPLIANCE),
    }
    base.update(overrides)
    return base


def test_pack_ohne_compliance_block_wird_abgelehnt():
    body = _pack()
    del body["compliance"]
    with pytest.raises(ValidationError):
        SitePack.model_validate(body)


def test_respect_robots_false_wird_abgelehnt():
    """README: nicht abschaltbar ohne dokumentierten Eintrag."""
    with pytest.raises(ValidationError):
        FetchSpec(respect_robots=False)


def test_personal_data_ohne_legal_basis_wird_abgelehnt():
    with pytest.raises(ValidationError, match="legal_basis"):
        ComplianceSpec.model_validate({**_COMPLIANCE, "personal_data": True})


def test_personal_data_mit_legal_basis_ist_erlaubt():
    spec = ComplianceSpec.model_validate(
        {**_COMPLIANCE, "personal_data": True, "legal_basis": "Art. 6 Abs. 1 lit. f DSGVO"}
    )
    assert spec.personal_data is True


def test_tos_verdict_forbids_wird_immer_abgelehnt():
    with pytest.raises(ValidationError, match="forbids"):
        ComplianceSpec.model_validate({**_COMPLIANCE, "tos_verdict": "forbids"})


@pytest.mark.parametrize("fehlt", ["reason", "approved_by", "approved_at", "evidence_url"])
def test_unvollstaendige_robots_ausnahme_wird_abgelehnt(fehlt):
    override = {
        "override": True,
        "reason": "Open-Data-Auftrag, robots irrtuemlich restriktiv",
        "approved_by": "human:sven",
        "approved_at": "2026-07-21",
        "evidence_url": "https://example.org/ticket/1",
    }
    del override[fehlt]
    with pytest.raises(ValidationError):
        FetchSpec(respect_robots=override)


def test_vollstaendige_robots_ausnahme_parst():
    f = FetchSpec(
        respect_robots={
            "override": True,
            "reason": "Open-Data-Auftrag",
            "approved_by": "human:sven",
            "approved_at": "2026-07-21",
            "evidence_url": "https://example.org/ticket/1",
        }
    )
    assert f.respect_robots is not True
    assert f.respect_robots.reason.startswith("Open-Data")
