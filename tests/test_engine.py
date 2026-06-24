"""Tests for the recommendation engine + learning loop."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from catalyst.db.models import (
    AttributePerformance,
    MetricSnapshot,
    Post,
    PostClassification,
    Recommendation,
)
from catalyst.ingest.pipeline import ensure_platform, ensure_source
from catalyst.recommend.engine import (
    generate_recommendations,
    score_recommendations,
    update_attribute_performance,
)


def _seed(session, platform, source, ext, topic, views, likes, comments) -> None:
    post = Post(
        platform_id=platform.id,
        source_id=source.id,
        external_id=ext,
        title=f"{topic} video",
        post_type="long",
        posted_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    session.add(post)
    session.flush()
    session.add(
        PostClassification(
            post_id=post.id, topic=topic, hook_type="how-to", angle="advanced-tactics",
            format="long", classifier_version="test",
        )
    )
    session.add(MetricSnapshot(post_id=post.id, score=likes, num_comments=comments, view_count=views))
    session.flush()


def _attr(session, platform_id, topic):
    return session.scalar(
        select(AttributePerformance).where(
            AttributePerformance.platform_id == platform_id,
            AttributePerformance.topic == topic,
            AttributePerformance.format == "",
            AttributePerformance.hook_type == "",
            AttributePerformance.angle == "",
        )
    )


def test_update_attribute_performance_and_trend(db_session) -> None:
    platform = ensure_platform(db_session, "youtube")
    source = ensure_source(db_session, platform, "UCeng")
    _seed(db_session, platform, source, "e1", "ZZ_A", views=100, likes=50, comments=50)  # eng 1.0
    _seed(db_session, platform, source, "e2", "ZZ_B", views=1000, likes=5, comments=5)  # eng 0.01

    update_attribute_performance(db_session, platform.id)
    a, b = _attr(db_session, platform.id, "ZZ_A"), _attr(db_session, platform.id, "ZZ_B")
    assert a is not None and b is not None
    assert a.avg_score > b.avg_score
    assert a.sample_size == 1

    # Re-running on identical data => trend ~0.
    update_attribute_performance(db_session, platform.id)
    db_session.refresh(a)
    assert abs(float(a.trend)) < 0.001


def test_score_recommendations_hit_and_miss(db_session) -> None:
    platform = ensure_platform(db_session, "youtube")
    for topic, score in [("ZZ_WIN", 100.0), ("ZZ_LOSE", 1.0)]:
        db_session.add(
            AttributePerformance(
                platform_id=platform.id, topic=topic, format="", hook_type="", angle="",
                avg_score=score, sample_size=5, trend=0,
            )
        )
    db_session.flush()
    db_session.add(Recommendation(cycle_id="c1", idea_text="win", topic="ZZ_WIN"))
    db_session.add(Recommendation(cycle_id="c1", idea_text="lose", topic="ZZ_LOSE"))
    db_session.flush()

    result = score_recommendations(db_session, platform.id, "c1")
    assert result == {"scored": 2, "hit_rate": 0.5}

    statuses = {
        r.topic: r.status
        for r in db_session.scalars(select(Recommendation).where(Recommendation.cycle_id == "c1"))
    }
    assert statuses == {"ZZ_WIN": "hit", "ZZ_LOSE": "miss"}


class _FakeGemini:
    def generate_json(self, prompt: str, **kwargs: object):
        return [
            {
                "title": "Make a how-to on SaaS pricing",
                "topic": "Pricing & Monetization",
                "format": "long",
                "hook": "how-to",
                "angle": "advanced-tactics",
                "suggested_day": "Tue",
                "reasoning": "Pricing how-tos resonate and the client under-indexes here.",
                "confidence": 0.8,
                "predicted_engagement_per_1k": 30,
            }
        ]


def test_generate_recommendations_maps_fields(db_session) -> None:
    platform = ensure_platform(db_session, "youtube")
    created = generate_recommendations(db_session, _FakeGemini(), platform.id, "cZ", n=1)
    assert created == 1

    rec = db_session.scalar(select(Recommendation).where(Recommendation.cycle_id == "cZ"))
    assert rec.idea_text.startswith("Make a how-to")
    assert rec.hook_type == "how-to"
    assert rec.predicted_score == 30.0
    assert rec.confidence == 0.8
    assert rec.model_version.startswith("gemini")
