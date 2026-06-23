"""ROI funnel: tie content performance to a downstream business outcome.

We can't see the client's real CRM, so we model an explicit, tunable funnel:

    reach (REAL: client video views)
      -> est. clicks      (x CTR)
      -> est. site visits (x visit-rate)
      -> est. demo requests (x demo-rate)
      -> est. pipeline $  (x deal value)

What makes it trustworthy: the **top of the funnel is real measured data** (client views), and every
conversion rate is an **explicit, versioned, client-editable assumption** (stored in
``funnel_assumptions``, surfaced as sliders on the dashboard). Plug in the client's real GA4/CRM
numbers and the estimate becomes exact.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from catalyst.analysis.metrics import _latest_snapshot_subq
from catalyst.db.models import FunnelAssumptions, FunnelSnapshot, Post

# Defaults are deliberately conservative and clearly labelled as assumptions.
DEFAULT_ASSUMPTIONS = {
    "name": "default",
    "ctr": 0.02,        # 2% of views click a description link
    "visit_rate": 0.9,  # 90% of clicks become a site session
    "demo_rate": 0.03,  # 3% of visits request a demo
    "deal_value": 1200.0,  # $ of pipeline per demo request
}


def compute_funnel(
    reach: float, ctr: float, visit_rate: float, demo_rate: float, deal_value: float
) -> dict:
    """Pure funnel math (no DB) — easy to test and to reason about."""
    clicks = reach * ctr
    visits = clicks * visit_rate
    demos = visits * demo_rate
    pipeline = demos * deal_value
    return {
        "reach": round(reach, 2),
        "est_clicks": round(clicks, 2),
        "est_visits": round(visits, 2),
        "est_demos": round(demos, 2),
        "est_pipeline_value": round(pipeline, 2),
    }


def get_or_create_default_assumptions(session: Session) -> FunnelAssumptions:
    existing = session.scalar(
        select(FunnelAssumptions).where(FunnelAssumptions.name == DEFAULT_ASSUMPTIONS["name"])
    )
    if existing is not None:
        return existing
    assumptions = FunnelAssumptions(**DEFAULT_ASSUMPTIONS)
    session.add(assumptions)
    session.flush()
    return assumptions


def client_reach(session: Session) -> float:
    """Total latest views across the client's content (the real top-of-funnel)."""
    latest = _latest_snapshot_subq()
    query = (
        select(func.coalesce(func.sum(latest.c.view_count), 0))
        .select_from(latest)
        .join(Post, Post.id == latest.c.post_id)
        .where(Post.is_client.is_(True))
    )
    return float(session.scalar(query) or 0)


def run_roi(
    session: Session,
    cycle_id: str | None = None,
    assumptions: FunnelAssumptions | None = None,
) -> dict:
    """Compute the funnel from current client reach and persist a snapshot."""
    assumptions = assumptions or get_or_create_default_assumptions(session)
    reach = client_reach(session)
    funnel = compute_funnel(
        reach, assumptions.ctr, assumptions.visit_rate, assumptions.demo_rate, assumptions.deal_value
    )
    cycle_id = cycle_id or "roi-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    session.add(
        FunnelSnapshot(
            cycle_id=cycle_id,
            reach=funnel["reach"],
            est_clicks=funnel["est_clicks"],
            est_visits=funnel["est_visits"],
            est_demos=funnel["est_demos"],
            est_pipeline_value=funnel["est_pipeline_value"],
            assumptions_id=assumptions.id,
        )
    )
    session.flush()
    return {**funnel, "cycle_id": cycle_id, "assumptions_id": assumptions.id}


def roi_once(assumptions: FunnelAssumptions | None = None) -> dict:
    from catalyst.db.session import session_scope

    with session_scope() as session:
        return run_roi(session, assumptions=assumptions)
