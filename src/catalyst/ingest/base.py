"""The platform-adapter interface.

Every platform we pull from (YouTube now; Reddit/others later) implements
:class:`PlatformAdapter`. The rest of the system speaks only in the normalized
shapes below, so the database, analysis, recommendations, and ROI never need to
know which platform the data came from. Adding a platform = one new adapter file.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class NormalizedPost:
    """A piece of content, mapped to our schema-neutral shape."""

    external_id: str
    title: str
    posted_at: datetime
    post_type: str  # short | long | livestream | text | link | ...
    body: str | None = None
    url: str | None = None
    permalink: str | None = None
    domain: str | None = None
    author: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: int | None = None
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class MetricReading:
    """A point-in-time metrics reading for a post."""

    external_id: str
    score: int  # likes (YouTube) / upvotes-minus-downvotes (Reddit)
    num_comments: int
    upvote_ratio: float | None = None
    awards: int = 0
    view_count: int | None = None


class PlatformAdapter(ABC):
    """Interface implemented by each platform."""

    #: Stored in the ``platforms`` table; identifies the data source.
    platform_name: str

    @abstractmethod
    def fetch_posts(self, source_key: str) -> list[NormalizedPost]:
        """Discover recent posts for a source (e.g. a channel's recent uploads)."""

    @abstractmethod
    def fetch_metrics(self, external_ids: list[str]) -> dict[str, MetricReading]:
        """Fetch current metrics for the given posts, keyed by external id.

        Called every cycle to append a fresh time-series snapshot.
        """
