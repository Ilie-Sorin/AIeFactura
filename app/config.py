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

    # Monitorizare (cap. 8) -- fisier de stare, verificat extern (ex. task
    # programat Windows, Nagios/Zabbix), plus email optional pentru alerte critice.
    status_file_path: str = "./data/status_monitorizare.json"
    integrity_check_interval_minutes: int = 60
    alerta_scanare_zile: int = 3

    # Stylesheet-ul XSLT oficial ANAF pentru vizualizarea RO-CIUS -- fisier
    # extern, NU distribuit in acest repo (vezi app/resources/README.md).
    anaf_stylesheet_path: str = "./app/resources/anaf_stylesheet.xsl"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_from: str = ""
    smtp_to: str = ""
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True

    @property
    def watch_directory_list(self) -> list[str]:
        return [d.strip() for d in self.watch_directories.split(";") if d.strip()]

    @property
    def own_cif_list(self) -> list[str]:
        return [c.strip() for c in self.own_cifs.split(";") if c.strip()]

    @property
    def smtp_to_list(self) -> list[str]:
        return [a.strip() for a in self.smtp_to.split(";") if a.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
