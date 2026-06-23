"""Analysis layer: surface what's working.

Everything is built on one idea — a per-post "performance" view that joins each post to its
**latest** metric snapshot and its classification. From there we aggregate by attribute (topic,
hook, angle, format), by posting time, and client-vs-field.

Performance signal:
- **engagement_rate** = (likes + comments) / views — rewards content that *resonates*, independent
  of channel size (so a small client can be compared fairly to big field channels).
- **avg_views** — reach, shown alongside.

We report ``engagement_per_1k`` = engagement_rate × 1000 (engagements per 1,000 views) because the
raw rate is a small decimal.
"""

from __future__ import annotations

from sqlalchemy import Float, cast, desc, func, select
from sqlalchemy.orm import Session

from catalyst.db.models import MetricSnapshot, Post, PostClassification

_DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _latest_snapshot_subq():
    """One row per post — its most recent metric snapshot (Postgres DISTINCT ON)."""
    return (
        select(MetricSnapshot)
        .distinct(MetricSnapshot.post_id)
        .order_by(MetricSnapshot.post_id, desc(MetricSnapshot.captured_at))
        .subquery()
    )


def _perf_base():
    """Per-post performance: classification attributes + latest metrics + engagement_rate."""
    latest = _latest_snapshot_subq()
    engagement = cast(latest.c.score + latest.c.num_comments, Float) / func.greatest(
        latest.c.view_count, 1
    )
    return (
        select(
            Post.id.label("post_id"),
            Post.is_client.label("is_client"),
            Post.posted_at.label("posted_at"),
            PostClassification.topic.label("topic"),
            PostClassification.hook_type.label("hook_type"),
            PostClassification.angle.label("angle"),
            PostClassification.format.label("format"),
            latest.c.view_count.label("views"),
            latest.c.score.label("likes"),
            latest.c.num_comments.label("comments"),
            engagement.label("engagement_rate"),
        )
        .join(latest, latest.c.post_id == Post.id)
        .join(PostClassification, PostClassification.post_id == Post.id)
        .subquery()
    )


def top_by_attribute(
    session: Session,
    attribute: str,
    *,
    client_only: bool | None = None,
    limit: int = 10,
) -> list[dict]:
    """Rank values of an attribute (topic/hook_type/angle/format) by engagement."""
    base = _perf_base()
    col = base.c[attribute]
    query = select(
        col.label("value"),
        func.count().label("n"),
        func.avg(base.c.views).label("avg_views"),
        func.avg(base.c.engagement_rate).label("avg_eng"),
    )
    if client_only is True:
        query = query.where(base.c.is_client.is_(True))
    elif client_only is False:
        query = query.where(base.c.is_client.is_(False))
    query = query.group_by(col).order_by(func.avg(base.c.engagement_rate).desc()).limit(limit)

    return [
        {
            "value": r.value,
            "n": r.n,
            "avg_views": round(float(r.avg_views or 0)),
            "engagement_per_1k": round(float(r.avg_eng or 0) * 1000, 2),
        }
        for r in session.execute(query)
    ]


def best_timing_by_day(session: Session) -> list[dict]:
    """Average engagement by day-of-week of publication."""
    base = _perf_base()
    dow = func.extract("dow", base.c.posted_at).label("dow")
    query = select(
        dow, func.count().label("n"), func.avg(base.c.engagement_rate).label("avg_eng")
    ).group_by(dow).order_by(dow)
    return [
        {
            "day": _DOW[int(r.dow)],
            "n": r.n,
            "engagement_per_1k": round(float(r.avg_eng or 0) * 1000, 2),
        }
        for r in session.execute(query)
    ]


def client_vs_field(session: Session, attribute: str = "format") -> list[dict]:
    """For each value of an attribute, compare client vs field engagement."""
    base = _perf_base()
    col = base.c[attribute]
    query = select(
        col.label("value"),
        base.c.is_client.label("is_client"),
        func.count().label("n"),
        func.avg(base.c.engagement_rate).label("avg_eng"),
    ).group_by(col, base.c.is_client)

    grouped: dict[str, dict] = {}
    for r in session.execute(query):
        entry = grouped.setdefault(r.value, {"value": r.value, "client": None, "field": None})
        side = "client" if r.is_client else "field"
        entry[side] = {"n": r.n, "engagement_per_1k": round(float(r.avg_eng or 0) * 1000, 2)}
    return list(grouped.values())


def summarize(session: Session) -> dict:
    """Bundle the headline insights — consumed by the recommender and the dashboard."""
    return {
        "top_topics": top_by_attribute(session, "topic"),
        "top_hooks": top_by_attribute(session, "hook_type"),
        "top_formats": top_by_attribute(session, "format"),
        "top_angles": top_by_attribute(session, "angle"),
        "timing_by_day": best_timing_by_day(session),
        "client_vs_field_format": client_vs_field(session, "format"),
    }
