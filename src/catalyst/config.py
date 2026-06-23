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
    youtube_api_key: str = Field(..., description="YouTube Data API v3 key (public-data API key).")
    anthropic_api_key: str = Field(..., description="Anthropic API key for Claude.")

    # --- Values with sensible defaults / optional ---
    youtube_channels: str = Field(
        default="",
        description="Comma-separated YouTube channel IDs or @handles to track.",
    )
    client_channel: str | None = Field(
        default=None,
        description="The channel ID/@handle treated as 'the client' (its videos are 'our' content).",
    )
    client_domain: str | None = Field(
        default=None,
        description="Domain treated as 'client' content (videos whose description links here).",
    )

    @property
    def channel_list(self) -> list[str]:
        """``youtube_channels`` split into a clean list."""
        return [c.strip() for c in self.youtube_channels.split(",") if c.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the singleton settings object (loaded once, then cached)."""
    return Settings()  # type: ignore[call-arg]  # values come from env/.env
