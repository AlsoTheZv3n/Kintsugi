"""Prueft kintsugi/config.py und haelt .env.example damit deckungsgleich."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from kintsugi.config import ConfigError, Settings, get_settings
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"


@pytest.fixture(autouse=True)
def _isolierte_umgebung(monkeypatch, tmp_path):
    """Keine echte .env und keine geerbten KINTSUGI_-Variablen im Test.

    Der Wechsel nach tmp_path ist wesentlich: `env_file=".env"` wird relativ
    zum Arbeitsverzeichnis aufgeloest, und eine lokale .env wuerde die
    Erwartungswerte hier still verschieben.
    """
    monkeypatch.chdir(tmp_path)
    for key in [k for k in os.environ if k.startswith("KINTSUGI_")]:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --------------------------------------------------------------------------
# Kontaktadresse
# --------------------------------------------------------------------------


def test_settings_baut_auch_ohne_kontakt():
    """Ohne KINTSUGI_CONTACT muss die Konstruktion gelingen.

    Waere sie hart, scheiterten `kintsugi --help` und ein offlines
    `alembic upgrade head --sql`, bevor sie irgendetwas tun.
    """
    settings = Settings()
    assert settings.contact is None


def test_require_contact_wirft_ohne_kontakt():
    with pytest.raises(ConfigError, match="KINTSUGI_CONTACT"):
        Settings().require_contact()


def test_user_agent_wirft_ohne_kontakt():
    """Kein User-Agent ohne Kontakt — damit ist die README-Zusage durchgesetzt."""
    with pytest.raises(ConfigError):
        _ = Settings().user_agent


@pytest.mark.parametrize("leer", ["", "   "])
def test_leerer_kontakt_zaehlt_als_nicht_gesetzt(leer):
    with pytest.raises(ConfigError):
        Settings(contact=leer).require_contact()


def test_user_agent_wird_erwartungsgemaess_gerendert():
    assert Settings(contact="ops@example.com").user_agent == "kintsugi/0.1 (+ops@example.com)"


# --------------------------------------------------------------------------
# Datenbank-URL
# --------------------------------------------------------------------------


def test_port_aus_der_umgebung_bewegt_die_url(monkeypatch):
    """Auf einer XAMPP-Maschine ist eine Kollision auf 5432 der Regelfall."""
    monkeypatch.setenv("KINTSUGI_PG_PORT", "55432")
    get_settings.cache_clear()
    assert get_settings().database_url.endswith(":55432/kintsugi")


def test_override_schlaegt_alle_einzelteile(monkeypatch):
    monkeypatch.setenv("KINTSUGI_PG_PORT", "55432")
    monkeypatch.setenv("KINTSUGI_DATABASE_URL_OVERRIDE", "postgresql+psycopg://a:b@c:1/d")
    get_settings.cache_clear()
    assert get_settings().database_url == "postgresql+psycopg://a:b@c:1/d"


def test_url_nutzt_den_synchronen_psycopg_treiber():
    """docs/01 und docs/08 sind gegenueber der README massgeblich: kein asyncpg."""
    assert Settings().database_url.startswith("postgresql+psycopg://")
    assert "asyncpg" not in Settings().database_url


def test_sonderzeichen_im_passwort_werden_kodiert():
    url = Settings(pg_password="p@ss/wo rd").database_url
    assert "p%40ss%2Fwo%20rd" in url


# --------------------------------------------------------------------------
# Geheimnisse
# --------------------------------------------------------------------------


def test_repr_gibt_keine_geheimnisse_preis():
    settings = Settings(pg_password="hochgeheim", anthropic_api_key="sk-ant-topsecret")
    text = repr(settings)
    assert "hochgeheim" not in text
    assert "sk-ant-topsecret" not in text


def test_geheimnisse_sind_ueber_get_secret_value_erreichbar():
    assert Settings(pg_password="hochgeheim").pg_password.get_secret_value() == "hochgeheim"


# --------------------------------------------------------------------------
# .env.example
# --------------------------------------------------------------------------


def test_env_example_deckt_sich_mit_den_feldern():
    """Kein fehlender und kein verwaister Schluessel."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    keys_in_file = set(re.findall(r"^(KINTSUGI_[A-Z0-9_]+)=", text, re.MULTILINE))
    keys_from_model = {f"KINTSUGI_{name.upper()}" for name in Settings.model_fields}

    assert keys_in_file - keys_from_model == set(), "verwaiste Schluessel in .env.example"
    assert keys_from_model - keys_in_file == set(), "fehlende Schluessel in .env.example"


def test_extra_felder_werden_abgelehnt():
    with pytest.raises(ValidationError, match=r"(?i)extra"):
        Settings(voellig_unbekannt="x")


# --------------------------------------------------------------------------
# Zugriff auf die Umgebung
# --------------------------------------------------------------------------


def test_nur_config_py_liest_die_prozessumgebung():
    """Sonst verteilt sich Konfiguration ueber die Codebasis."""
    offenders = []
    for path in (PROJECT_ROOT / "kintsugi").rglob("*.py"):
        if path.name == "config.py":
            continue
        text = path.read_text(encoding="utf-8")
        for needle in ("os.environ", "os.getenv"):
            if needle in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {needle}")
    assert not offenders, "Umgebungszugriff ausserhalb von config.py:\n" + "\n".join(offenders)
