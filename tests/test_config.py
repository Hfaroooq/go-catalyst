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
    "REDDIT_CLIENT_ID": "client-id",
    "REDDIT_CLIENT_SECRET": "client-secret",
    "ANTHROPIC_API_KEY": "sk-ant-test",
}


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.database_url == REQUIRED_ENV["DATABASE_URL"]
    assert settings.reddit_client_id == "client-id"
    assert settings.anthropic_api_key == "sk-ant-test"
    # Default applies when not provided.
    assert settings.tracked_subreddits == "SaaS,startups,Entrepreneur"
    assert settings.subreddit_list == ["SaaS", "startups", "Entrepreneur"]


def test_missing_required_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    # Remove one required secret.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_subreddit_list_handles_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TRACKED_SUBREDDITS", " SaaS , startups ,, Entrepreneur ")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.subreddit_list == ["SaaS", "startups", "Entrepreneur"]
