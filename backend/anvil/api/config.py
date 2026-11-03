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
        env_file=".env",
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

    @property
    def database_dsn(self) -> str:
        if self.database_url:
            return self.database_url

        password = quote_plus(self.postgres_password.get_secret_value())
        return f"postgresql+psycopg://anvil:{password}@localhost:5433/anvil"


@lru_cache
def get_settings() -> Settings:
    return Settings()
