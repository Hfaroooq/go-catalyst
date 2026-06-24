"""Analysis tests.

Uses distinctive synthetic topic labels (ZZ_*) so assertions don't depend on whatever real
data is already in the database; runs in the rolled-back ``db_session``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from catalyst.db.models import MetricSnapshot, Post, PostClassification
from catalyst.ingest.pipeline import ensure_platform, ensure_source
from catalyst.analysis.metrics import summarize, top_by_attribute


def _seed_post(session, platform, source, ext_id, topic, views, likes, comments) -> None:
    post = Post(
        platform_id=platform.id,
        source_id=source.id,
        external_id=ext_id,
        title=f"{topic} video",
        post_type="long",
        posted_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    session.add(post)
    session.flush()
    session.add(
        PostClassification(
            post_id=post.id,
            topic=topic,
            hook_type="how-to",
            angle="advanced-tactics",
            format="long",
            classifier_version="test",
        )
    )
    session.add(
        MetricSnapshot(post_id=post.id, score=likes, num_comments=comments, view_count=views)
    )
    session.flush()


def test_top_by_attribute_ranks_by_engagement(db_session) -> None:
    platform = ensure_platform(db_session, "youtube")
    source = ensure_source(db_session, platform, "UCmetrics")

    # ZZ_HIGH: engagement_rate 1.0 (100 eng / 100 views). ZZ_LOW: 0.01 (10 / 1000).
    _seed_post(db_session, platform, source, "zz_h1", "ZZ_HIGH", views=100, likes=50, comments=50)
    _seed_post(db_session, platform, source, "zz_h2", "ZZ_HIGH", views=200, likes=100, comments=100)
    _seed_post(db_session, platform, source, "zz_l1", "ZZ_LOW", views=1000, likes=5, comments=5)

    # High limit so our synthetic ZZ_* topics aren't crowded out of the top-N by real data.
    topics = top_by_attribute(db_session, "topic", limit=500)
    ranked = {row["value"]: i for i, row in enumerate(topics)}
    assert ranked["ZZ_HIGH"] < ranked["ZZ_LOW"]  # higher engagement ranks first

    by_value = {row["value"]: row for row in topics}
    assert by_value["ZZ_HIGH"]["engagement_per_1k"] == 1000.0  # 1.0 * 1000
    assert by_value["ZZ_HIGH"]["n"] == 2
    assert by_value["ZZ_LOW"]["engagement_per_1k"] == 10.0  # 0.01 * 1000


def test_summarize_returns_all_sections(db_session) -> None:
    keys = summarize(db_session).keys()
    assert {
        "top_topics",
        "top_hooks",
        "top_formats",
        "top_angles",
        "timing_by_day",
        "client_vs_field_format",
    } <= set(keys)
