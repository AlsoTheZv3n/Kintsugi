"""Zentrale, typisierte Konfiguration.

Einziger Ort im Projekt, an dem die Prozessumgebung gelesen wird. Ein Test
unter ``tests/unit/test_config.py`` erzwingt das: kein Modul unter
``kintsugi/`` ausser diesem darf ``os.environ`` oder ``os.getenv`` benutzen.
Sonst verteilt sich die Konfiguration ueber die Codebasis und laesst sich
weder dokumentieren noch im Test kontrolliert setzen.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class ConfigError(RuntimeError):
    """Eine benoetigte Einstellung fehlt oder ist unbrauchbar."""


class Settings(BaseSettings):
    """Laufzeitkonfiguration, vollstaendig aus ``KINTSUGI_``-Variablen."""

    model_config = SettingsConfigDict(
        env_prefix="KINTSUGI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    # --- Datenbank ---------------------------------------------------------
    # Der Port steht als eigenes Feld und nicht als Literal in einer
    # vorgefertigten URL: auf dieser Maschine laeuft XAMPP, eine Kollision auf
    # 5432 ist der Regelfall und nicht die Ausnahme. KINTSUGI_PG_PORT muss
    # deshalb die URL bewegen, die die Anwendung tatsaechlich waehlt.
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "kintsugi"
    pg_password: SecretStr = SecretStr("kintsugi")
    pg_db: str = "kintsugi"
    # SecretStr, nicht str: eine von aussen gereichte URL traegt in aller Regel
    # ein Passwort im Klartext mit sich.
    database_url_override: SecretStr | None = None

    # --- Ablage ------------------------------------------------------------
    snapshot_root: Path = Path("./data/bronze")
    packs_dir: Path = Path("packs")

    # --- Netz und Identitaet ----------------------------------------------
    user_agent_product: str = "kintsugi/0.1"
    contact: str | None = None
    http_timeout_s: float = 20.0

    # --- Sonstiges ---------------------------------------------------------
    log_level: str = "INFO"
    anthropic_api_key: SecretStr | None = Field(default=None, repr=False)

    @property
    def database_url(self) -> SecretStr:
        """SQLAlchemy-URL auf dem synchronen psycopg3-Treiber.

        Bewusst ``SecretStr`` und kein ``str``. Die URL enthaelt das Passwort im
        Klartext; gaebe sie es als gewoehnliche Zeichenkette heraus, waere
        ``pg_password: SecretStr`` reine Kosmetik. Eine solche URL landet sonst
        im ``repr`` der SQLAlchemy-Engine, in Verbindungsfehlern, in der
        Alembic-Ausgabe und — seit ``kintsugi/logging.py`` — in jeder
        JSON-Logzeile, die sie beilaeufig mitfuehrt.

        Wer die URL wirklich braucht, ruft ``.get_secret_value()`` auf. Damit
        ist das Offenlegen eine bewusste Handlung statt eines Versehens. Zum
        Protokollieren und Anzeigen gibt es ``database_url_masked``.

        ``database_url_override`` hat Vorrang vor allen Einzelteilen — fuer CI
        und fuer Faelle, in denen die URL komplett von aussen kommt.
        """
        if self.database_url_override:
            return self.database_url_override
        user = quote(self.pg_user, safe="")
        password = quote(self.pg_password.get_secret_value(), safe="")
        return SecretStr(
            f"postgresql+psycopg://{user}:{password}@{self.pg_host}:{self.pg_port}/{self.pg_db}"
        )

    @property
    def database_url_masked(self) -> str:
        """Dieselbe URL mit verdecktem Passwort — fuer Logs und Fehlermeldungen.

        Die Maskierung uebernimmt SQLAlchemy selbst, statt sie mit einem
        eigenen regulaeren Ausdruck nachzubauen. Das deckt auch eine von aussen
        gereichte ``database_url_override`` in beliebiger Schreibweise ab.
        """
        return make_url(self.database_url.get_secret_value()).render_as_string(hide_password=True)

    def require_contact(self) -> str:
        """Kontaktadresse fuer den User-Agent, oder ein harter Fehler.

        Die Pruefung sitzt bewusst hier und nicht in der Konstruktion von
        ``Settings``. Waere sie dort, wuerden ``kintsugi --help`` und ein
        offlines ``alembic upgrade head --sql`` scheitern, bevor sie
        irgendetwas tun — und kein CI-Job setzt die Variable. Die Zusage aus
        der README bleibt trotzdem durchgesetzt, weil kein Request ohne
        User-Agent hinausgeht und der User-Agent ohne Kontakt nicht existiert.
        """
        if not self.contact or not self.contact.strip():
            raise ConfigError(
                "KINTSUGI_CONTACT ist nicht gesetzt. Die README verpflichtet das "
                "Projekt auf einen identifizierbaren User-Agent mit Kontaktadresse; "
                "ohne sie darf keine Anfrage hinausgehen."
            )
        return self.contact.strip()

    @property
    def user_agent(self) -> str:
        """Identifizierbarer User-Agent, siehe README Abschnitt Compliance."""
        return f"{self.user_agent_product} (+{self.require_contact()})"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Zwischengespeicherte Einstellungen. In Tests mit ``cache_clear()`` leeren."""
    return Settings()
