from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    port: int = 8000
    api_admin_token: str = Field(default="", repr=False)
    dashboard_origin: str = "http://localhost:3000"
    supabase_url: str = ""
    supabase_secret_key: str = Field(default="", repr=False)
    telegram_api_id: int | None = None
    telegram_api_hash: str = Field(default="", repr=False)
    tdlib_library_path: str = "/usr/local/lib/libtdjson.so"
    tdlib_database_directory: str = "/data/tdlib/db"
    tdlib_files_directory: str = "/data/tdlib/files"
    worker_id: str = "open-tgate-worker-1"
    heartbeat_interval_seconds: int = 30
    external_send_enabled: bool = False

    @property
    def production_ready(self) -> bool:
        return bool(
            self.api_admin_token
            and self.supabase_url
            and self.supabase_secret_key
            and self.telegram_api_id
            and self.telegram_api_hash
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

