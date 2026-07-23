"""Setzt ADR-014 durch: Geheimnisse verlassen config.py nur als SecretStr.

Der ausloesende Fehler war eine Property mit Geheimnisbezug, die einen `str`
zurueckgab — womit `pg_password: SecretStr` wirkungslos war und das Passwort in
Engine-repr, Verbindungsfehler, Alembic-Ausgabe und JSON-Log sickern konnte
(derselbe Fehlerraum, den docs/05-api.md fuer 5xx ausschliesst).

Der Test prueft drei Dinge: dass keine geheimnisbezogene Property einen `str`
zurueckgibt, dass keine Standarddarstellung von Settings den Klartext enthaelt,
und — als Gegenprobe — dass der Introspektions-Guard eine absichtlich lecke
Klasse tatsaechlich meldet. Er liest nur getrackten Code, nie docs/.
"""

from __future__ import annotations

import re
import typing
from typing import get_type_hints

from kintsugi import config
from kintsugi.config import Settings
from pydantic import SecretStr

# Namensmuster, die ein Geheimnis verraten. `url` ist dabei, weil eine
# Datenbank-URL das Passwort im Klartext mitfuehrt.
SECRET_NAME_RE = re.compile(r"password|secret|token|api_key|url", re.IGNORECASE)

PLAINTEXT_PW = "streng-geheimes-passwort-42"
PLAINTEXT_KEY = "sk-ant-streng-geheimer-schluessel"


def _is_secretstr_type(annotation: object) -> bool:
    """True, wenn die Annotation SecretStr oder Optional[SecretStr] ist."""
    if annotation is SecretStr:
        return True
    args = typing.get_args(annotation)
    return bool(args) and any(arg is SecretStr for arg in args)


def _leaky_secret_properties(cls: type) -> list[str]:
    """Namen geheimnisbezogener Properties, die nicht SecretStr zurueckgeben.

    Als `_masked` gekennzeichnete Properties duerfen str liefern — sie sind der
    ausdrueckliche, verdeckte Anzeigeweg.
    """
    offenders: list[str] = []
    for name, attr in vars(cls).items():
        if not isinstance(attr, property) or attr.fget is None:
            continue
        if not SECRET_NAME_RE.search(name) or name.endswith("_masked"):
            continue
        hints = get_type_hints(attr.fget)
        if not _is_secretstr_type(hints.get("return")):
            offenders.append(name)
    return offenders


# --------------------------------------------------------------------------
# Introspektion von Settings
# --------------------------------------------------------------------------


def test_keine_geheimnis_property_gibt_str_zurueck():
    offenders = _leaky_secret_properties(Settings)
    assert not offenders, f"Property mit Geheimnisbezug gibt kein SecretStr zurueck: {offenders}"


def test_geheimnisbezogene_felder_sind_secretstr():
    """pg_password, anthropic_api_key, database_url_override tragen kein Klartext-str."""
    offenders = []
    for name, field in Settings.model_fields.items():
        if SECRET_NAME_RE.search(name) and not _is_secretstr_type(field.annotation):
            offenders.append(name)
    assert not offenders, f"geheimnisbezogenes Feld ist nicht SecretStr: {offenders}"


def test_database_url_ist_secretstr_property():
    """Gezielt der Fall, der ADR-014 ausgeloest hat."""
    prop = vars(Settings)["database_url"]
    assert isinstance(prop, property)
    assert _is_secretstr_type(get_type_hints(prop.fget)["return"])


# --------------------------------------------------------------------------
# Laufzeit-Leckprobe
# --------------------------------------------------------------------------


def test_keine_standarddarstellung_zeigt_den_klartext():
    settings = Settings(pg_password=PLAINTEXT_PW, anthropic_api_key=PLAINTEXT_KEY)
    darstellungen = {
        "repr": repr(settings),
        "str": str(settings),
        "model_dump": str(settings.model_dump()),
        "model_dump_json": settings.model_dump_json(),
        "database_url": str(settings.database_url),
        "database_url_masked": settings.database_url_masked,
    }
    for name, text in darstellungen.items():
        assert PLAINTEXT_PW not in text, f"Passwort sichtbar in {name}"
        assert PLAINTEXT_KEY not in text, f"API-Schluessel sichtbar in {name}"


def test_klartext_bleibt_bewusst_erreichbar():
    """Der Guard darf nicht einfach 'nie erreichbar' belohnen."""
    settings = Settings(pg_password=PLAINTEXT_PW)
    assert PLAINTEXT_PW in settings.database_url.get_secret_value()
    assert settings.pg_password.get_secret_value() == PLAINTEXT_PW


# --------------------------------------------------------------------------
# Gegenprobe: beisst der Guard ueberhaupt?
# --------------------------------------------------------------------------


def test_guard_meldet_eine_absichtlich_lecke_property():
    class Leck:
        @property
        def database_url(self) -> str:  # str statt SecretStr — das Leck
            return "postgresql+psycopg://u:geheim@h/db"

    offenders = _leaky_secret_properties(Leck)
    assert "database_url" in offenders


def test_guard_verschont_maskierte_property():
    class Sauber:
        @property
        def database_url(self) -> SecretStr:
            return SecretStr("x")

        @property
        def database_url_masked(self) -> str:  # ausdruecklich verdeckt -> erlaubt
            return "postgresql+psycopg://u:***@h/db"

    assert _leaky_secret_properties(Sauber) == []


def test_config_modul_hat_die_maskierte_fassung():
    """database_url_masked existiert als Anzeigeweg."""
    assert isinstance(vars(config.Settings).get("database_url_masked"), property)
