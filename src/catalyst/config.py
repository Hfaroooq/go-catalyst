"""Central configuration.

All secrets and tunables are loaded from environment variables (or a local
``.env`` file that is never committed). Nothing in the codebase reads
``os.environ`` directly — everything goes through :func:`get_settings` so there
is exactly one place that knows what configuration exists and which values are
required.

Required values have no default, so the app fails loudly at startup if a secret
is missing, rather than silently misbehaving later.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed view of our environment configuration.

    Field names map to UPPER_CASE env vars automatically (e.g. ``database_url``
    reads ``DATABASE_URL``), case-insensitively.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Required secrets (no defaults: missing -> startup error) ---
    database_url: str = Field(..., description="Postgres connection string (Supabase).")
    reddit_client_id: str = Field(..., description="Reddit app client id.")
    reddit_client_secret: str = Field(..., description="Reddit app client secret.")
    anthropic_api_key: str = Field(..., description="Anthropic API key for Claude.")

    # --- Values with sensible defaults ---
    reddit_user_agent: str = Field(
        default="catalyst-tracker/0.1 by u/your_reddit_username",
        description="Reddit requires a descriptive User-Agent on every request.",
    )
    tracked_subreddits: str = Field(
        default="SaaS,startups,Entrepreneur",
        description="Comma-separated subreddits to track (no 'r/' prefix).",
    )
    client_domain: str | None = Field(
        default=None,
        description="Domain treated as the 'client' (posts linking here are 'our' content).",
    )

    @property
    def subreddit_list(self) -> list[str]:
        """``tracked_subreddits`` split into a clean list."""
        return [s.strip() for s in self.tracked_subreddits.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the singleton settings object (loaded once, then cached)."""
    return Settings()  # type: ignore[call-arg]  # values come from env/.env
