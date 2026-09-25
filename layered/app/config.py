# Every tunable value in the app, read from .env once at startup.
#
# Any layer may import `settings`. Nothing else anywhere reads environment
# variables directly.
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Defaults match .env.example. Port 5434 is this week's container;
    # week 1's Postgres is on 5433.
    database_url: str = "postgresql://checkout:checkout@localhost:5434/self_checkout"
    low_stock_default_threshold: int = 50
    popularity_snapshot_cap: int = 100

    # The popularity window size and slide interval are not here on purpose.
    # They live in db/schema.sql as column defaults on popularity_state, and
    # the analytics layer reads them back from that row.


# The .env file is read when this line runs, so every importer shares one
# already-validated copy.
settings = Settings()
