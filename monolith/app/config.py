from pydantic_settings import BaseSettings, SettingsConfigDict


# Auto-fills its fields from .env / environment variables on creation.
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Each field's default matches .env.example; .env overrides if present.
    database_url: str = "postgresql://checkout:checkout@localhost:5433/self_checkout"
    window_size: int = 1000
    slide_interval: int = 500
    low_stock_default_threshold: int = 50
    popularity_snapshot_cap: int = 100


# One shared instance - other files import this instead of re-reading .env.
settings = Settings()