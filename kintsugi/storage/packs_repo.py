"""Repository fuer Site-Pack-Zeilen (SQLAlchemy Core, synchrones psycopg3).

Die YAML-Datei ist die menschliche Quelle; hier wird das validierte Modell als
``spec``-JSONB in die ``site_pack``-Tabelle hochgeschrieben (docs/03 §Site-Packs,
docs/02 §Lebenszyklus). Kein lokaler psql-Client — jede Pruefung von Hand geht
ueber ``docker compose exec`` oder den Treiber.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import Connection, Row, func, select

from kintsugi.packs.model import SitePack
from kintsugi.storage.tables import site_pack

CREATED_BY = "human:sven"


def _serialise(pack: SitePack) -> dict[str, Any]:
    return pack.model_dump(mode="json", by_alias=True)


def _comparison_key(spec: dict[str, Any]) -> dict[str, Any]:
    # Die Versionsnummer besitzt die Datenbank, nicht das YAML — sie darf einen
    # Idempotenz-Vergleich nicht verschieben.
    return {k: v for k, v in spec.items() if k != "version"}


def _newest(conn: Connection, domain: str, entity: str) -> Row[Any] | None:
    return conn.execute(
        select(site_pack.c.id, site_pack.c.version, site_pack.c.spec)
        .where(site_pack.c.domain == domain, site_pack.c.entity == entity)
        .order_by(site_pack.c.version.desc())
        .limit(1)
    ).one_or_none()


def upsert_pack(conn: Connection, pack: SitePack) -> uuid.UUID:
    """Schreibt den Pack als neue Version — oder no-op, wenn unveraendert.

    Idempotenz: ein erneutes Sync unveraenderter YAML erzeugt keine neue
    Version. Verglichen wird der serialisierte spec ohne die Versionsnummer.
    """
    serialised = _serialise(pack)
    newest = _newest(conn, pack.domain, pack.entity)
    if newest is not None and _comparison_key(newest.spec) == _comparison_key(serialised):
        return cast(uuid.UUID, newest.id)  # unveraendert

    new_version = (newest.version + 1) if newest is not None else 1
    spec_to_store = {**serialised, "version": new_version}
    pack_id: uuid.UUID = conn.execute(
        site_pack.insert()
        .values(
            domain=pack.domain,
            entity=pack.entity,
            version=new_version,
            status="draft",
            spec=spec_to_store,
            created_by=CREATED_BY,
        )
        .returning(site_pack.c.id)
    ).scalar_one()
    return pack_id


def activate(conn: Connection, pack_id: uuid.UUID) -> None:
    """Promotet eine Version auf ``active`` — in genau dieser Reihenfolge.

    docs/03 macht ``site_pack_one_active`` zu einem PARTIELLEN Unique-Index, und
    einen partiellen Unique-Index kann PostgreSQL nicht DEFERRABLE machen. Ein
    naives „neue aktiv setzen, dann alte zuruecknehmen" verletzt den Index
    mitten in der Transaktion und bricht ab. Deshalb ZUERST die aktuelle aktive
    Zeile auf retired setzen, DANN die neue aktivieren. Nicht umsortieren.
    """
    target = conn.execute(
        select(site_pack.c.domain, site_pack.c.entity).where(site_pack.c.id == pack_id)
    ).one()

    conn.execute(
        site_pack.update()
        .where(
            site_pack.c.domain == target.domain,
            site_pack.c.entity == target.entity,
            site_pack.c.status == "active",
        )
        .values(status="retired", retired_at=func.now())
    )
    conn.execute(
        site_pack.update()
        .where(site_pack.c.id == pack_id)
        .values(status="active", activated_at=func.now())
    )


def get_active(conn: Connection, domain: str, entity: str) -> Row[Any] | None:
    return conn.execute(
        select(site_pack).where(
            site_pack.c.domain == domain,
            site_pack.c.entity == entity,
            site_pack.c.status == "active",
        )
    ).one_or_none()


def list_packs(conn: Connection) -> list[Row[Any]]:
    return list(
        conn.execute(
            select(site_pack).order_by(
                site_pack.c.domain, site_pack.c.entity, site_pack.c.version
            )
        ).all()
    )
