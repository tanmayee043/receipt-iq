"""Configuration, from environment variables or a local .env file, checked up front.

There are deliberately no default addresses: a worker that's missing REDIS_URL should
stop with a clear error, not quietly talk to whatever is on localhost. Copy .env.example
to .env for local development.

Secrets stay in real environment variables, never in .env or the repo.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """What both the worker and the API need."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    receipt_iq_data: Path = Path("data")
    # How long Redis waits before handing a crashed worker's receipt to another worker. Has
    # to outlast the longest task and retry wait, or a healthy task gets delivered twice.
    celery_visibility_timeout: int = 3600


class ApiSettings(Settings):
    """What only the API needs, so a worker doesn't have to know the owner's password hash."""

    # From scripts/hash_password.py.
    owner_password_hash: str = Field(pattern=r"^\$argon2id\$")
    # Off only for plain-http local testing; browsers won't send a Secure cookie otherwise.
    cookie_secure: bool = True
    session_hours: int = 8
    # Keeps the API's sessions and counters apart from Celery's Redis databases, 0 and 1.
    api_redis_db: int = 2
    # Requests per minute, per IP.
    rate_limit_general: int = 120
    rate_limit_upload: int = 10
    rate_limit_login: int = 5
    # The built screens (npm run build). Served if present; the API works without them.
    frontend_dist: Path = Path("frontend/dist")


def get_settings() -> Settings:
    return Settings()
