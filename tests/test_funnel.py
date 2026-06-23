"""ROI funnel tests: pure math, persistence, and client-only reach."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from catalyst.db.models import FunnelSnapshot, MetricSnapshot, Post
from catalyst.ingest.pipeline import ensure_platform, ensure_source
from catalyst.roi.funnel import (
    client_reach,
    compute_funnel,
    get_or_create_default_assumptions,
    run_roi,
)


def test_compute_funnel_math() -> None:
    result = compute_funnel(100_000, ctr=0.02, visit_rate=0.9, demo_rate=0.03, deal_value=1200)
    assert result["est_clicks"] == 2000.0
    assert result["est_visits"] == 1800.0
    assert result["est_demos"] == 54.0
    assert result["est_pipeline_value"] == 64_800.0


def test_default_assumptions_idempotent(db_session) -> None:
    a1 = get_or_create_default_assumptions(db_session)
    a2 = get_or_create_default_assumptions(db_session)
    assert a1.id == a2.id


def _seed(session, platform, source, ext, views, *, is_client) -> None:
    post = Post(
        platform_id=platform.id,
        source_id=source.id,
        external_id=ext,
        title="roi video",
        post_type="long",
        is_client=is_client,
        posted_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    session.add(post)
    session.flush()
    session.add(MetricSnapshot(post_id=post.id, score=1, num_comments=1, view_count=views))
    session.flush()


def test_client_reach_counts_only_client(db_session) -> None:
    platform = ensure_platform(db_session, "youtube")
    source = ensure_source(db_session, platform, "UCroi")
    before = client_reach(db_session)

    _seed(db_session, platform, source, "roi_client", 10_000, is_client=True)
    _seed(db_session, platform, source, "roi_field", 99_999, is_client=False)

    after = client_reach(db_session)
    assert after == before + 10_000  # field views excluded


def test_run_roi_persists_consistent_snapshot(db_session) -> None:
    assumptions = get_or_create_default_assumptions(db_session)
    reach = client_reach(db_session)
    expected = compute_funnel(
        reach, assumptions.ctr, assumptions.visit_rate, assumptions.demo_rate, assumptions.deal_value
    )

    result = run_roi(db_session)
    assert result["reach"] == expected["reach"]
    assert result["est_pipeline_value"] == expected["est_pipeline_value"]

    snapshot = db_session.scalar(
        select(FunnelSnapshot).where(FunnelSnapshot.cycle_id == result["cycle_id"])
    )
    assert snapshot is not None
    assert snapshot.est_pipeline_value == expected["est_pipeline_value"]
    assert snapshot.assumptions_id == assumptions.id
