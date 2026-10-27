"""Configuration ancrée au projet ou au fichier .env explicite."""
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_file_encoding="utf-8")
    opensearch_url: str = "https://localhost:9200"
    opensearch_username: str = ""
    opensearch_password: SecretStr = SecretStr("")
    opensearch_verify_ssl: bool = True
    opensearch_ca_cert: str = ""
    opensearch_timeout: int = 30
    index_prefix: str = "backup"
    mappings_dir: Path = Path("mappings")
    log_level: str = "INFO"
    history_max_versions: int = 10

    @field_validator("opensearch_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        try:
            url = urlsplit(value)
            port = url.port
        except ValueError:
            raise ValueError("URL OpenSearch invalide") from None
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username or url.password or url.query or url.fragment
                or url.path not in {"", "/"} or port == 0):
            raise ValueError("URL HTTP(S) attendue, sans identifiants ni chemin")
        return value

    @field_validator("opensearch_timeout")
    @classmethod
    def positive_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("timeout strictement positif attendu")
        return value

    @field_validator("index_prefix")
    @classmethod
    def valid_prefix(cls, value: str) -> str:
        import re
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
            raise ValueError("préfixe invalide")
        return value

    @field_validator("history_max_versions")
    @classmethod
    def nonnegative_history(cls, value: int) -> int:
        if value < 0 or value > 2147483647:
            raise ValueError("limite historique entière entre 0 et 2147483647 attendue")
        return value

    @field_validator("log_level")
    @classmethod
    def valid_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("niveau de journalisation invalide")
        return value


def load_settings(env_file: Path | None = None) -> Settings:
    path = env_file.expanduser().resolve() if env_file else PROJECT_ROOT / ".env"
    if env_file and not path.is_file():
        raise ValueError("Fichier .env explicite introuvable")
    settings = Settings(_env_file=path)
    base = path.parent
    settings.mappings_dir = (base / settings.mappings_dir).resolve()
    if settings.opensearch_ca_cert:
        ca = (base / settings.opensearch_ca_cert).resolve()
        if not ca.is_file():
            raise ValueError("Certificat CA introuvable")
        settings.opensearch_ca_cert = str(ca)
    return settings
