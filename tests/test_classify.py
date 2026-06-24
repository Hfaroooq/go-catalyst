"""Tests for classification: heuristics (no network) + the orchestration with a fake LLM."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import func, select

from catalyst.db.models import Post, PostClassification
from catalyst.ingest.pipeline import ensure_platform, ensure_source
from catalyst.recommend.classify import (
    CLASSIFIER_VERSION,
    classify_angles,
    classify_hook,
    classify_topic,
    run_classification,
)


def test_classify_hook() -> None:
    assert classify_hook("How to grow your SaaS in 2026") == "how-to"
    assert classify_hook("7 ways to reduce churn") == "listicle"
    assert classify_hook("Notion vs Obsidian: which wins?") == "comparison"
    assert classify_hook("Stop doing cold outreach like this") == "contrarian"
    assert classify_hook("How we grew to $1M ARR") == "case-study"
    assert classify_hook("Why your funnel is broken") == "question"
    assert classify_hook("Introducing our new AI agent") == "announcement"
    assert classify_hook("A deep look at marketing analytics") == "informational"


def test_classify_topic() -> None:
    assert classify_topic("Advanced SEO and backlink strategies", None, None) == "SEO"
    assert classify_topic("Build an AI agent with chatgpt automation", None, None) == "AI & Automation"
    assert classify_topic("Best cooking recipes for dinner", None, None) == "Other"
    # Tags contribute to the score.
    assert classify_topic("Our latest video", None, ["pricing", "mrr", "subscription"]) == "Pricing & Monetization"


class FakeGemini:
    """Returns a fixed angle for every id it finds in the prompt."""

    def __init__(self, angle: str = "data-driven") -> None:
        self.angle = angle
        self.calls = 0

    def generate_json(self, prompt: str, **kwargs: object) -> list[dict[str, str]]:
        self.calls += 1
        ids = re.findall(r'"id":\s*"([^"]+)"', prompt)
        return [{"id": i, "angle": self.angle} for i in ids]


def test_classify_angles_validates_options() -> None:
    fake = FakeGemini(angle="data-driven")
    out = classify_angles(fake, [("1", "t", "d"), ("2", "t", "d")])
    assert out == {"1": "data-driven", "2": "data-driven"}

    bogus = type("B", (), {"generate_json": lambda self, p, **k: [{"id": "1", "angle": "nonsense"}]})()
    assert classify_angles(bogus, [("1", "t", "d")]) == {"1": "other"}


def _make_post(session, platform, source, ext_id, title, tags) -> Post:
    post = Post(
        platform_id=platform.id,
        source_id=source.id,
        external_id=ext_id,
        title=title,
        body="some description",
        post_type="long",
        posted_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        raw={"snippet": {"tags": tags}},
    )
    session.add(post)
    session.flush()
    return post


def test_run_classification_is_idempotent(db_session) -> None:
    # Clear the slate: classify anything already present (e.g. real ingested posts)
    # so this test's assertions depend only on the two posts it adds.
    run_classification(db_session, client=FakeGemini())

    platform = ensure_platform(db_session, "youtube")
    source = ensure_source(db_session, platform, "UCtest")
    p1 = _make_post(db_session, platform, source, "vtest1", "How to do SEO", ["seo", "keyword"])
    p2 = _make_post(db_session, platform, source, "vtest2", "7 tips for cold email", ["email marketing"])

    count = run_classification(db_session, client=FakeGemini(angle="advanced-tactics"))
    assert count == 2  # only the two new posts

    rows = list(
        db_session.scalars(
            select(PostClassification).where(PostClassification.post_id.in_([p1.id, p2.id]))
        )
    )
    assert len(rows) == 2
    assert {r.topic for r in rows} == {"SEO", "Email Marketing"}
    assert all(r.classifier_version == CLASSIFIER_VERSION for r in rows)
    assert all(r.angle == "advanced-tactics" for r in rows)
    assert all(r.format == "long" for r in rows)

    # Re-running classifies nothing new (idempotent).
    assert run_classification(db_session, client=FakeGemini()) == 0


class _RaisingGemini:
    """Simulates a persistently failing LLM (e.g. a 503)."""

    def generate_json(self, prompt: str, **kwargs: object):
        raise RuntimeError("503 high demand")


def test_run_classification_survives_batch_failure(db_session) -> None:
    platform = ensure_platform(db_session, "youtube")
    source = ensure_source(db_session, platform, "UCfail")
    _make_post(db_session, platform, source, "vfail", "How to do SEO", ["seo"])

    # Every batch's LLM call raises -> batches are skipped, nothing crashes,
    # and the post stays unclassified so it's retried on a later run.
    assert run_classification(db_session, client=_RaisingGemini()) == 0
    classified_ids = set(db_session.scalars(select(PostClassification.post_id)))
    post = db_session.scalar(select(Post).where(Post.external_id == "vfail"))
    assert post.id not in classified_ids
