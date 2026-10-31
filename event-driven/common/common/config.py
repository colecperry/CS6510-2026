# Settings, read from .env once at startup, split so a service can only see its own.
#
# There is one class per service rather than one shared object. A single object carrying all three database URLs would let any service connect to any store by reading a different attribute, which is the coupling this week exists to remove.
# Each class uses an env prefix, so InventorySettings.database_url reads INVENTORY_DATABASE_URL and cannot see the others at all.
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Each service starts from its own directory, so a relative path would look for .env in the wrong place. This finds the one at the workspace root.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _config(prefix: str = "") -> SettingsConfigDict:
    # extra="ignore" matters: every class reads the same .env, so each must tolerate the other services' variables being present.
    return SettingsConfigDict(
        env_file=_ENV_FILE, env_file_encoding="utf-8", env_prefix=prefix, extra="ignore"
    )


class SharedSettings(BaseSettings):
    """Values that are part of the contract's behaviour, so all services must agree on them."""

    model_config = _config()

    low_stock_default_threshold: int = 50
    popularity_snapshot_cap: int = 100

    # Not prefixed, because all three services connect to the same broker. It is the one address they share, and the only one any of them holds besides its own database.
    broker_url: str = "amqp://guest:guest@localhost:5672/"


class CheckoutSettings(BaseSettings):
    """Checkout's own settings. It no longer holds any service's address, because it no longer calls one."""

    model_config = _config("CHECKOUT_")

    database_url: str = "postgresql://checkout:checkout@localhost:5440/self_checkout"
    pool_max: int = 100

    # How often the relay looks for sales it has committed to announcing but not yet sent. No shopper waits on this, so it is tuned for how quickly stock catches up rather than for request latency.
    outbox_poll_ms: int = 50


class InventorySettings(BaseSettings):
    """Inventory's own settings. It calls nobody, so it has no addresses."""

    model_config = _config("INVENTORY_")

    database_url: str = "postgresql://checkout:checkout@localhost:5441/self_checkout"
    pool_max: int = 20


class AnalyticsSettings(BaseSettings):
    """Analytics' own settings. It calls nobody either; scans reach it through the broker."""

    model_config = _config("ANALYTICS_")

    database_url: str = "postgresql://checkout:checkout@localhost:5442/self_checkout"
    pool_max: int = 10


# Built on import, so the .env file is read once and every importer shares one validated copy.
# A service constructs only the one it needs: importing another service's settings is visible in the import line, which is what makes the boundary test able to catch it.
shared = SharedSettings()
