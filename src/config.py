from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

class Settings(BaseSettings):
    db_user: str
    db_password: str = Field(min_length=1)
    db_name: str
    db_host: str
    db_port: int = 5432

    telegram_bot_token: str = Field(min_length=30)
    log_level: str = "INFO"

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()