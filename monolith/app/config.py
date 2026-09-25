# Every tunable value in the app, read from .env once at startup.
#
# Other files import `settings` rather than reading environment variables
# themselves.
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Defaults match .env.example; .env overrides any of them.
    database_url: str = "postgresql://checkout:checkout@localhost:5433/self_checkout"
    window_size: int = 1000
    slide_interval: int = 500
    low_stock_default_threshold: int = 50
    popularity_snapshot_cap: int = 100


# The .env file is read when this line runs, so every importer shares one
# already-validated copy.
settings = Settings()
