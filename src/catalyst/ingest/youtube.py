"""YouTube Data API v3 adapter.

We call the REST API directly with ``httpx`` rather than pulling in the large
``google-api-python-client``: the endpoints we need are simple authenticated GETs,
so a thin client is clearer and lighter (a deliberate build-vs-buy call).

Quota note: ``channels``/``playlistItems``/``videos`` list calls cost 1 unit each,
so tracking a handful of channels stays far under the 10,000 units/day free quota.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterator

import httpx

from catalyst.config import get_settings
from catalyst.ingest.base import MetricReading, NormalizedPost, PlatformAdapter

BASE_URL = "https://www.googleapis.com/youtube/v3"

_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")
_URL_RE = re.compile(r"https?://([^/\s)]+)")
_THUMB_PRIORITY = ("maxres", "standard", "high", "medium", "default")


class YouTubeAPIError(RuntimeError):
    """Raised when the YouTube API returns a non-200 response."""


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested without any network)
# --------------------------------------------------------------------------- #
def parse_duration(iso: str | None) -> int | None:
    """Convert an ISO-8601 duration (e.g. ``PT12M34S``) to whole seconds."""
    if not iso:
        return None
    match = _DURATION_RE.fullmatch(iso)
    if not match:
        return None  # e.g. "P0D" for live broadcasts
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def classify_format(duration_seconds: int | None, live_broadcast_content: str | None) -> str:
    """Map a video to a content format: short | long | livestream."""
    if live_broadcast_content in ("live", "upcoming"):
        return "livestream"
    if duration_seconds is not None and duration_seconds <= 60:
        return "short"  # heuristic: YouTube doesn't expose a clean Shorts flag
    return "long"


def primary_domain(text: str | None) -> str | None:
    """Return the host of the first URL in ``text`` (used to tag client content)."""
    if not text:
        return None
    match = _URL_RE.search(text)
    if not match:
        return None
    host = match.group(1).lower()
    return host[4:] if host.startswith("www.") else host


def best_thumbnail(thumbnails: dict[str, Any]) -> str | None:
    """Pick the highest-resolution thumbnail URL available."""
    for key in _THUMB_PRIORITY:
        if key in thumbnails and thumbnails[key].get("url"):
            return thumbnails[key]["url"]
    return None


def _parse_published(value: str | None) -> datetime:
    """Parse an ISO-8601 timestamp (YouTube uses a trailing 'Z')."""
    if not value:
        raise ValueError("missing publishedAt")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize_video(item: dict[str, Any]) -> NormalizedPost:
    """Turn a YouTube ``videos.list`` item into our neutral NormalizedPost."""
    snippet = item.get("snippet", {})
    content = item.get("contentDetails", {})
    video_id = item["id"]
    duration = parse_duration(content.get("duration"))
    description = snippet.get("description")
    return NormalizedPost(
        external_id=video_id,
        title=snippet.get("title", ""),
        body=description,
        posted_at=_parse_published(snippet.get("publishedAt")),
        post_type=classify_format(duration, snippet.get("liveBroadcastContent")),
        url=f"https://www.youtube.com/watch?v={video_id}",
        permalink=f"https://www.youtube.com/watch?v={video_id}",
        domain=primary_domain(description),
        author=snippet.get("channelTitle"),
        thumbnail_url=best_thumbnail(snippet.get("thumbnails", {})),
        duration_seconds=duration,
        raw=item,
    )


def metric_from_item(item: dict[str, Any]) -> MetricReading:
    """Extract a metrics reading from a ``videos.list`` item's statistics."""
    stats = item.get("statistics", {})
    return MetricReading(
        external_id=item["id"],
        score=int(stats.get("likeCount") or 0),
        num_comments=int(stats.get("commentCount") or 0),
        view_count=int(stats.get("viewCount") or 0),
    )


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# --------------------------------------------------------------------------- #
# The adapter
# --------------------------------------------------------------------------- #
class YouTubeAdapter(PlatformAdapter):
    platform_name = "youtube"

    def __init__(
        self,
        api_key: str,
        max_videos_per_channel: int = 50,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.max_videos = max_videos_per_channel
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=30.0)

    @classmethod
    def from_settings(cls, max_videos_per_channel: int = 50) -> "YouTubeAdapter":
        return cls(get_settings().youtube_api_key, max_videos_per_channel)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self._client.get(path, params={**params, "key": self.api_key})
        if response.status_code != 200:
            raise YouTubeAPIError(f"{response.status_code} for {path}: {response.text[:300]}")
        return response.json()

    def _search_channel_id(self, query: str) -> str | None:
        """Find a channel id by name when a handle doesn't resolve (a YouTube quirk)."""
        data = self._get(
            "/search",
            {"part": "snippet", "type": "channel", "q": query, "maxResults": 1},
        )
        items = data.get("items", [])
        return items[0]["id"]["channelId"] if items else None

    def _resolve_channel(self, key: str) -> tuple[str, str, str]:
        """Resolve a channel id or @handle to (channel_id, title, uploads_playlist_id).

        Prefers the cheap (1-unit) id/handle lookup, and falls back to search
        (100 units) only when a handle doesn't match — so a slightly-off handle
        self-heals instead of crashing the run.
        """
        params: dict[str, Any] = {"part": "contentDetails,snippet"}
        if key.startswith("UC"):
            params["id"] = key
        else:
            params["forHandle"] = key if key.startswith("@") else f"@{key}"
        data = self._get("/channels", params)
        items = data.get("items", [])

        if not items and not key.startswith("UC"):
            channel_id = self._search_channel_id(key.lstrip("@"))
            if channel_id:
                data = self._get(
                    "/channels", {"part": "contentDetails,snippet", "id": channel_id}
                )
                items = data.get("items", [])

        if not items:
            raise YouTubeAPIError(f"channel not found: {key}")
        item = items[0]
        uploads = item["contentDetails"]["relatedPlaylists"]["uploads"]
        return item["id"], item["snippet"]["title"], uploads

    def fetch_posts(self, source_key: str) -> list[NormalizedPost]:
        _, _, uploads = self._resolve_channel(source_key)

        # Page through the uploads playlist (handles pagination + the cap).
        video_ids: list[str] = []
        page_token: str | None = None
        while len(video_ids) < self.max_videos:
            params: dict[str, Any] = {
                "part": "contentDetails",
                "playlistId": uploads,
                "maxResults": 50,
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._get("/playlistItems", params)
            for it in data.get("items", []):
                video_ids.append(it["contentDetails"]["videoId"])
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        video_ids = video_ids[: self.max_videos]

        posts: list[NormalizedPost] = []
        for batch in _chunks(video_ids, 50):
            data = self._get(
                "/videos", {"part": "snippet,contentDetails", "id": ",".join(batch)}
            )
            posts.extend(normalize_video(it) for it in data.get("items", []))
        return posts

    def fetch_metrics(self, external_ids: list[str]) -> dict[str, MetricReading]:
        readings: dict[str, MetricReading] = {}
        for batch in _chunks(external_ids, 50):
            if not batch:
                continue
            data = self._get("/videos", {"part": "statistics", "id": ",".join(batch)})
            for it in data.get("items", []):
                readings[it["id"]] = metric_from_item(it)
        return readings
