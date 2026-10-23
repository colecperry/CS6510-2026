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


class CheckoutSettings(BaseSettings):
    """Checkout's own settings. The only service that holds another service's address, because paying has to reduce stock that inventory owns."""

    model_config = _config("CHECKOUT_")

    database_url: str = "postgresql://checkout:checkout@localhost:5437/self_checkout"
    pool_max: int = 100

    inventory_url: str = "http://localhost:8082"
    analytics_url: str = "http://localhost:8083"

    # How long to keep trying inventory during a payment before giving up and refusing it.
    # Kept well under the load client's 10 second per-request timeout, so a failure comes back as a refusal we can explain rather than a request the client abandoned.
    stock_call_timeout_ms: int = 500
    stock_retry_budget_ms: int = 2000


class InventorySettings(BaseSettings):
    """Inventory's own settings. It calls nobody, so it has no addresses."""

    model_config = _config("INVENTORY_")

    database_url: str = "postgresql://checkout:checkout@localhost:5438/self_checkout"
    pool_max: int = 20


class AnalyticsSettings(BaseSettings):
    """Analytics' own settings. It calls nobody either; scans are pushed to it."""

    model_config = _config("ANALYTICS_")

    database_url: str = "postgresql://checkout:checkout@localhost:5439/self_checkout"
    pool_max: int = 10


# Built on import, so the .env file is read once and every importer shares one validated copy.
# A service constructs only the one it needs: importing another service's settings is visible in the import line, which is what makes the boundary test able to catch it.
shared = SharedSettings()
