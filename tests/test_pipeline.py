"""Dedup + snapshot tests for the ingestion pipeline.

Uses a fake adapter (no network) and the rolled-back ``db_session`` fixture, so
it verifies real Postgres behaviour without leaving any data behind.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from catalyst.db.models import MetricSnapshot, Post
from catalyst.ingest.base import MetricReading, NormalizedPost, PlatformAdapter
from catalyst.ingest.pipeline import run_ingest


class FakeAdapter(PlatformAdapter):
    platform_name = "youtube"

    def __init__(self, posts: list[NormalizedPost], metrics: dict[str, MetricReading]) -> None:
        self._posts = posts
        self._metrics = metrics

    def fetch_posts(self, source_key: str) -> list[NormalizedPost]:
        return list(self._posts)

    def fetch_metrics(self, external_ids: list[str]) -> dict[str, MetricReading]:
        return {k: v for k, v in self._metrics.items() if k in external_ids}


def _sample_post() -> NormalizedPost:
    return NormalizedPost(
        external_id="vid1",
        title="Test video",
        posted_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        post_type="long",
        domain="acme.com",
        author="Acme SaaS",
    )


def test_same_post_not_duplicated_but_snapshots_accumulate(db_session) -> None:
    post = _sample_post()

    run1 = run_ingest(
        FakeAdapter([post], {"vid1": MetricReading("vid1", score=10, num_comments=2, view_count=100)}),
        db_session,
        ["@acme"],
        client_channel="@acme",
    )
    assert run1["posts_new"] == 1
    assert run1["snapshots_taken"] == 1

    # Second cycle: same post, new metrics.
    run2 = run_ingest(
        FakeAdapter([post], {"vid1": MetricReading("vid1", score=25, num_comments=5, view_count=300)}),
        db_session,
        ["@acme"],
        client_channel="@acme",
    )
    assert run2["posts_new"] == 0  # dedup: no new post row
    assert run2["snapshots_taken"] == 1

    # Exactly one post row, two snapshots (the time-series).
    post_row = db_session.scalar(select(Post).where(Post.external_id == "vid1"))
    assert post_row is not None
    assert post_row.is_client is True  # source matched client_channel

    snapshot_count = db_session.scalar(
        select(func.count()).select_from(MetricSnapshot).where(MetricSnapshot.post_id == post_row.id)
    )
    assert snapshot_count == 2


def test_is_client_via_domain(db_session) -> None:
    post = _sample_post()  # domain == acme.com
    run_ingest(
        FakeAdapter([post], {"vid1": MetricReading("vid1", score=1, num_comments=0, view_count=5)}),
        db_session,
        ["@somefield"],  # not the client channel
        client_channel="@notus",
        client_domain="acme.com",  # but the link domain matches the client
    )
    post_row = db_session.scalar(select(Post).where(Post.external_id == "vid1"))
    assert post_row.is_client is True
