"""Tests for configuration loading.

These verify the two behaviours we care about:
1. When all required env vars are present, settings load with correct values
   (and defaults apply where expected).
2. When a required secret is missing, loading fails loudly.

We pass ``_env_file=None`` so the tests never accidentally read a developer's
real ``.env`` file — they depend only on the env vars we set here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from catalyst.config import Settings

REQUIRED_ENV = {
    "DATABASE_URL": "postgresql://user:pass@localhost:5432/catalyst",
    "YOUTUBE_API_KEY": "yt-api-key",
    "GEMINI_API_KEY": "gemini-key",
}


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.database_url == REQUIRED_ENV["DATABASE_URL"]
    assert settings.youtube_api_key == "yt-api-key"
    assert settings.gemini_api_key == "gemini-key"
    # Defaults apply when not provided.
    assert settings.youtube_channels == ""
    assert settings.channel_list == []
    assert settings.client_channel is None


def test_missing_required_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    # Remove one required secret.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_channel_list_handles_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("YOUTUBE_CHANNELS", " @hubspot , UC123 ,, @notion ")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.channel_list == ["@hubspot", "UC123", "@notion"]
