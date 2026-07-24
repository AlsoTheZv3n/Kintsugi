"""Drei orthogonale Outcome-Enums und die escalate_on-Abbildung (I1.4.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from kintsugi.classify.enums import (
    ESCALATE_ON_TO_INCIDENT_KIND,
    HarnessOutcome,
    IncidentKind,
    PrecheckVerdict,
)
from kintsugi.packs.loader import load_pack


def test_enums_sind_keine_strings():
    assert PrecheckVerdict.ok != "ok"
    assert HarnessOutcome.no_action != "no_action"
    assert isinstance(PrecheckVerdict.ok, str) is False
    assert isinstance(HarnessOutcome.no_action, str) is False
    # Serialisierung nur ueber .value.
    assert PrecheckVerdict.ok.value == "ok"


def _shipped_packs():
    packs = []
    for meta in sorted(Path("packs").rglob("*.yaml")):
        domain, entity = meta.parent.name, meta.stem
        packs.append(load_pack(domain, entity, root=Path("packs")))
    return packs


def test_escalate_on_bildet_injektiv_auf_incident_kind():
    for pack in _shipped_packs():
        tokens = pack.healing.escalate_on
        mapped = [ESCALATE_ON_TO_INCIDENT_KIND[token] for token in tokens]
        # Jedes Token bekannt und die Abbildung injektiv (keine Kollision).
        assert len(set(mapped)) == len(mapped)
        # Identitaet, kein Kollaps: enum_violation -> enum_violation.
        if "enum_violation" in tokens:
            assert ESCALATE_ON_TO_INCIDENT_KIND["enum_violation"] is IncidentKind.enum_violation


@pytest.mark.parametrize(
    "token", ["field_removed", "schema_change", "enum_violation", "natural_key_broken"]
)
def test_jedes_token_hat_gleichnamigen_incident_kind(token):
    assert ESCALATE_ON_TO_INCIDENT_KIND[token].value == token


@pytest.mark.integration
def test_incident_kind_labels_stammen_aus_migration_0001():
    from kintsugi.storage.db import get_engine
    from sqlalchemy import text

    eng = get_engine()
    try:
        with eng.connect() as conn:
            labels = set(
                conn.execute(text("SELECT unnest(enum_range(NULL::incident_kind))::text")).scalars()
            )
    except Exception as exc:
        pytest.skip(f"kein postgres erreichbar: {exc}")
    assert "enum_violation" in labels
    assert "natural_key_broken" in labels
    assert labels == {k.value for k in IncidentKind}  # der Python-Spiegel deckt sich
