"""FixtureMeta: eine Fixture bildet 1:1 auf eine Bronze-``snapshot``-Zeile ab.

docs/07-test-targets.md §Stufe 0 und docs/03-data-model.md §Bronze. ``meta.json``
spiegelt die Bronze-Spalten, damit eine Fixture ohne Umrechnung als snapshot-
Zeile eingespielt werden kann. ``content_hash`` ist der sha256-Hex des
**unkomprimierten** Koerpers — derselbe Wert, den ``save_snapshot`` bildet.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

__all__ = ["FixtureMeta"]

# baseline oder edge:<slug> mit slug aus [a-z0-9_].
_LABEL_RE = re.compile(r"^(baseline|edge:[a-z0-9_]+)$")


class FixtureMeta(BaseModel):
    """Metadaten einer Golden-Fixture."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    fetched_at: datetime
    http_status: int
    content_type: str | None
    content_hash: str
    byte_size: int
    fetcher: str
    golden_label: str
    synthetic: bool = False
    derived_from: str | None = None
    edit: str | None = None

    @field_validator("fetched_at")
    @classmethod
    def _tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("fetched_at braucht eine Zeitzone (UTC)")
        return value.astimezone(UTC)

    @field_validator("content_hash")
    @classmethod
    def _sha256_hex(cls, value: str) -> str:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("content_hash muss 64 Hex-Zeichen sein (sha256)")
        return value

    @field_validator("golden_label")
    @classmethod
    def _label_shape(cls, value: str) -> str:
        if not _LABEL_RE.match(value):
            raise ValueError("golden_label muss 'baseline' oder 'edge:<slug>' sein")
        return value

    @model_validator(mode="after")
    def _synthetic_needs_provenance(self) -> FixtureMeta:
        # Eine synthetische Fixture ohne Herkunft ist nicht nachvollziehbar —
        # docs/07 verlangt einen dokumentierten derived_from/edit-Beleg.
        if self.synthetic and not (self.derived_from and self.derived_from.strip()):
            raise ValueError("synthetic: true braucht ein nicht-leeres derived_from")
        return self
