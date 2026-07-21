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
    database_url_override: str | None = None

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
    def database_url(self) -> str:
        """SQLAlchemy-URL auf dem synchronen psycopg3-Treiber.

        ``database_url_override`` hat Vorrang vor allen Einzelteilen — fuer CI
        und fuer Faelle, in denen die URL komplett von aussen kommt.
        """
        if self.database_url_override:
            return self.database_url_override
        user = quote(self.pg_user, safe="")
        password = quote(self.pg_password.get_secret_value(), safe="")
        return f"postgresql+psycopg://{user}:{password}@{self.pg_host}:{self.pg_port}/{self.pg_db}"

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
