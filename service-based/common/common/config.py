# Every tunable value, read from .env once at startup.
#
# Each setting is looked for in three places, and the first answer wins:
#
#   1. a variable typed in the terminal
#   2. the .env file at the service-based/ root
#   3. the default written below
#
# Shared by all four processes. The services read the database settings; only the gateway reads the service addresses.
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Each service starts from its own directory, so a relative path would look for .env in the wrong place. This finds the one at the workspace root.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8")

    # Defaults match .env.example. Port 5436 is this week's container.
    database_url: str = "postgresql://checkout:checkout@localhost:5436/self_checkout"
    low_stock_default_threshold: int = 50
    popularity_snapshot_cap: int = 100

    # Where the gateway forwards to. No domain service reads these; knowing another service's address is exactly what they are not allowed to do.
    checkout_url: str = "http://localhost:8081"
    inventory_url: str = "http://localhost:8082"
    analytics_url: str = "http://localhost:8083"

    # One database, three pools. Previous weeks gave a single process 120 connections against a server limit of 200; three processes each taking that would exhaust it. Split by traffic instead.
    checkout_pool_max: int = 100
    inventory_pool_max: int = 10
    analytics_pool_max: int = 10


# The .env file is read when this line runs, so every importer shares one already-validated copy.
settings = Settings()
