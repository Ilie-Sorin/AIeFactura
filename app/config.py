from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://aiefactura_app:aiefactura_app_dev@localhost:5433/aiefactura"
    database_url_migrations: str = "postgresql+psycopg://aiefactura:aiefactura_dev@localhost:5433/aiefactura"
    secret_key: str = "dev-only-insecure-key"
    watch_directories: str = ""
    scan_root: str = "./data/scan"
    environment: str = "development"

    # CIF-urile proprii (separate prin ;) -- determina directia (intrare/iesire)
    # a unei facturi: emitent in aceasta lista => iesire, altfel intrare.
    own_cifs: str = ""

    @property
    def watch_directory_list(self) -> list[str]:
        return [d.strip() for d in self.watch_directories.split(";") if d.strip()]

    @property
    def own_cif_list(self) -> list[str]:
        return [c.strip() for c in self.own_cifs.split(";") if c.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
