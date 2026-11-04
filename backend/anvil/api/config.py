import sys
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


def normalize_database_url(database_url: str) -> str:
    if sys.platform != "win32":
        return database_url
    url = make_url(database_url)
    if url.host == "localhost":
        return url.set(host="127.0.0.1").render_as_string(hide_password=False)
    return database_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_prefix="ANVIL_",
        extra="ignore",
    )

    database_url: str | None = None
    postgres_password: SecretStr = Field(
        default=SecretStr("anvil"),
        validation_alias="POSTGRES_PASSWORD",
    )
    demo_token: SecretStr = SecretStr("")
    demo_user_id: str = "demo"
    demo_user_email: str = "demo@anvil.local"
    sandbox_reaper_enabled: bool = True
    sandbox_reaper_interval_seconds: int = 300
    sandbox_max_age_hours: float = 2.0
    instance_id: str = Field(
        default="",
        validation_alias="ANVIL_INSTANCE_ID",
    )

    @property
    def owner_id(self) -> str:
        if self.instance_id:
            return self.instance_id
        dsn = self.database_dsn
        if "/" in dsn:
            return dsn.rsplit("/", 1)[-1].split("?")[0] or "default"
        return "default"

    @property
    def database_dsn(self) -> str:
        if self.database_url:
            return self.database_url

        password = quote_plus(self.postgres_password.get_secret_value())
        return f"postgresql+psycopg://anvil:{password}@localhost:5433/anvil"


@lru_cache
def get_settings() -> Settings:
    return Settings()
