"""A thin Google Gemini client (free tier).

Used for the genuinely-fuzzy LLM steps only: the "angle" tag during
classification, and idea generation later. Per-row structured fields are done
with cheap local code (see ``classify.py``) — we spend the LLM where judgment
actually pays.

We force JSON output (``responseMimeType``) so parsing is reliable, and retry on
transient rate-limit / server errors.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from catalyst.config import get_settings

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash-lite"


class GeminiError(RuntimeError):
    """Raised when Gemini returns an error or unparseable output."""


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self._client = client or httpx.Client(base_url=GEMINI_BASE, timeout=60.0)

    @classmethod
    def from_settings(cls, model: str = DEFAULT_MODEL) -> "GeminiClient":
        return cls(get_settings().gemini_api_key, model)

    def generate_json(self, prompt: str, *, system: str | None = None, retries: int = 5) -> Any:
        """Call Gemini and parse the response as JSON."""
        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.2,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        last_error = "unknown error"
        for attempt in range(retries):
            response = self._client.post(
                f"/models/{self.model}:generateContent",
                params={"key": self.api_key},
                json=body,
            )
            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates")
                if not candidates:
                    raise GeminiError(f"no candidates in response: {json.dumps(data)[:300]}")
                parts = candidates[0].get("content", {}).get("parts", [])
                text = "".join(part.get("text", "") for part in parts)
                try:
                    return json.loads(text)
                except json.JSONDecodeError as exc:
                    raise GeminiError(f"non-JSON response: {text[:200]}") from exc
            last_error = f"{response.status_code}: {response.text[:200]}"
            # Retry only transient overload (500/503). Do NOT retry 429: it's a quota
            # error that won't clear in seconds, and retrying just burns more quota.
            if response.status_code in (500, 503):
                time.sleep(min(2 ** attempt, 20))  # exponential backoff: 1,2,4,8,16s
                continue
            break
        raise GeminiError(last_error)
