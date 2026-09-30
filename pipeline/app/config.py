# Every tunable value in the app.
#
# Each setting is looked for in three places, and the first answer wins:
#
#   1. a variable typed in the terminal, e.g. DATABASE_URL=... uv run ...
#   2. the .env file
#   3. the default written below
#
# Any layer may import `settings`. Nothing else anywhere reads environment
# variables directly.
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # This line is what puts the .env file on that list. Without it only the
    # terminal and the defaults would be checked, and .env would be ignored.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # The values below are step 3, the fallbacks. They match .env.example, so
    # the app still runs if .env is missing. Port 5435 is this week's
    # container; week 1's Postgres is on 5433 and week 2's on 5434.
    database_url: str = "postgresql://checkout:checkout@localhost:5435/self_checkout"
    low_stock_default_threshold: int = 50
    popularity_snapshot_cap: int = 100

    # The popularity window size and slide interval are not here on purpose.
    # They live in db/schema.sql as column defaults on popularity_state, and
    # the analytics layer reads them back from that row.


# Nothing is read until this line runs. It does the looking-up described
# above, once, and every importer shares the result.
settings = Settings()
