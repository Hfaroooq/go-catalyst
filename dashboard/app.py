"""Catalyst Content Tracker — Streamlit dashboard.

A read-only view over the database. It does no analysis itself: the worker computes and persists
everything; this just reads and displays. Three tabs: Performance, Recommendations, ROI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the `catalyst` package (under ./src) is importable even if the project
# isn't pip-installed (e.g. on Streamlit Community Cloud).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import streamlit as st

# Make Streamlit Cloud secrets visible to catalyst.config (which reads env vars).
# Locally there's no secrets file, so this is a no-op and config falls back to .env.
try:
    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key, str(_value))
except Exception:
    pass

import pandas as pd
from sqlalchemy import desc, func, select

from catalyst.analysis.metrics import summarize
from catalyst.db.models import JobRun, MetricSnapshot, Post, Recommendation, Source
from catalyst.db.session import session_scope
from catalyst.roi.funnel import DEFAULT_ASSUMPTIONS, client_reach, compute_funnel

st.set_page_config(page_title="Catalyst Content Tracker", page_icon="📊", layout="wide")


@st.cache_data(ttl=120)
def load_overview() -> dict:
    with session_scope() as s:
        last = s.execute(
            select(JobRun.started_at, JobRun.status).order_by(desc(JobRun.started_at)).limit(1)
        ).first()
        return {
            "posts": s.scalar(select(func.count()).select_from(Post)) or 0,
            "client_posts": s.scalar(
                select(func.count()).select_from(Post).where(Post.is_client.is_(True))
            ) or 0,
            "snapshots": s.scalar(select(func.count()).select_from(MetricSnapshot)) or 0,
            "sources": s.scalar(select(func.count()).select_from(Source)) or 0,
            "last_run": str(last[0]) if last else "—",
            "last_status": last[1] if last else "—",
        }


@st.cache_data(ttl=120)
def load_summary() -> dict:
    with session_scope() as s:
        return summarize(s)


@st.cache_data(ttl=120)
def load_recommendations() -> dict:
    with session_scope() as s:
        cycle_id = s.scalar(
            select(Recommendation.cycle_id).order_by(desc(Recommendation.created_at)).limit(1)
        )
        recs = []
        if cycle_id:
            for r in s.scalars(select(Recommendation).where(Recommendation.cycle_id == cycle_id)):
                recs.append({
                    "idea": r.idea_text, "topic": r.topic, "format": r.format, "hook": r.hook_type,
                    "angle": r.angle, "confidence": r.confidence, "predicted": r.predicted_score,
                    "reasoning": r.reasoning,
                })
        counts: dict[str, dict] = {}
        for c_id, status, n in s.execute(
            select(Recommendation.cycle_id, Recommendation.status, func.count())
            .group_by(Recommendation.cycle_id, Recommendation.status)
        ):
            counts.setdefault(c_id, {}).update({status: n})
        hit_rates = []
        for c_id in sorted(counts):
            hits, misses = counts[c_id].get("hit", 0), counts[c_id].get("miss", 0)
            if hits + misses:
                hit_rates.append({"cycle": c_id[-6:], "hit_rate": round(hits / (hits + misses), 2)})
        return {"cycle_id": cycle_id, "recs": recs, "hit_rates": hit_rates}


@st.cache_data(ttl=120)
def load_client_reach() -> float:
    with session_scope() as s:
        return client_reach(s)


# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #
st.title("📊 Catalyst Content Tracker")
overview = load_overview()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Tracked videos", f"{overview['posts']:,}")
c2.metric("Client videos", f"{overview['client_posts']:,}")
c3.metric("Metric snapshots", f"{overview['snapshots']:,}")
c4.metric("Last cycle", overview["last_status"])
st.caption(f"Channels tracked: {overview['sources']} · last run: {overview['last_run']} (UTC)")

tab_perf, tab_recs, tab_roi = st.tabs(["📈 Performance", "💡 Recommendations", "💰 ROI"])

# --------------------------------------------------------------------------- #
# Performance
# --------------------------------------------------------------------------- #
with tab_perf:
    summary = load_summary()
    left, right = st.columns(2)
    with left:
        st.markdown("**Top topics** (engagement per 1k views)")
        st.dataframe(pd.DataFrame(summary["top_topics"]), hide_index=True, width="stretch")
        st.markdown("**Top hooks**")
        st.dataframe(pd.DataFrame(summary["top_hooks"]), hide_index=True, width="stretch")
    with right:
        st.markdown("**Format performance**")
        st.dataframe(pd.DataFrame(summary["top_formats"]), hide_index=True, width="stretch")
        st.markdown("**Top angles**")
        st.dataframe(pd.DataFrame(summary["top_angles"]), hide_index=True, width="stretch")

    if summary["top_topics"]:
        st.markdown("**Topic engagement**")
        st.bar_chart(pd.DataFrame(summary["top_topics"]).set_index("value")["engagement_per_1k"])
    if summary["timing_by_day"]:
        st.markdown("**Best posting day** (avg engagement per 1k)")
        st.bar_chart(pd.DataFrame(summary["timing_by_day"]).set_index("day")["engagement_per_1k"])

    st.markdown("**Client vs field** (by format)")
    cf_rows = [
        {
            "format": r["value"],
            "client (eng/1k)": (r["client"] or {}).get("engagement_per_1k"),
            "field (eng/1k)": (r["field"] or {}).get("engagement_per_1k"),
        }
        for r in summary["client_vs_field_format"]
    ]
    st.dataframe(pd.DataFrame(cf_rows), hide_index=True, width="stretch")

# --------------------------------------------------------------------------- #
# Recommendations
# --------------------------------------------------------------------------- #
with tab_recs:
    rec = load_recommendations()
    st.subheader(f"Latest recommendations — cycle {rec['cycle_id']}")
    if not rec["recs"]:
        st.info("No recommendations yet — run a cycle.")
    for r in rec["recs"]:
        with st.expander(f"💡 {r['idea']}"):
            st.write(
                f"**Topic:** {r['topic']} · **Format:** {r['format']} · "
                f"**Hook:** {r['hook']} · **Angle:** {r['angle']}"
            )
            st.write(f"**Confidence:** {r['confidence']} · **Predicted engagement/1k:** {r['predicted']}")
            if r["reasoning"]:
                st.write(r["reasoning"])
    if rec["hit_rates"]:
        st.markdown("**Recommender hit-rate over cycles** — did its attribute bets keep winning?")
        st.line_chart(pd.DataFrame(rec["hit_rates"]).set_index("cycle")["hit_rate"])

# --------------------------------------------------------------------------- #
# ROI
# --------------------------------------------------------------------------- #
with tab_roi:
    st.subheader("Content → pipeline")
    reach = load_client_reach()
    st.metric("Client reach (real views)", f"{int(reach):,}")
    st.caption("The top of the funnel is real measured data. Every rate below is an editable assumption.")

    s1, s2, s3, s4 = st.columns(4)
    ctr = s1.slider("CTR (views → clicks)", 0.0, 0.20, float(DEFAULT_ASSUMPTIONS["ctr"]), 0.005)
    visit = s2.slider("Visit rate (clicks → visits)", 0.0, 1.0, float(DEFAULT_ASSUMPTIONS["visit_rate"]), 0.05)
    demo = s3.slider("Demo rate (visits → demos)", 0.0, 0.20, float(DEFAULT_ASSUMPTIONS["demo_rate"]), 0.005)
    deal = s4.number_input("Deal value ($ / demo)", value=float(DEFAULT_ASSUMPTIONS["deal_value"]), step=100.0)

    funnel = compute_funnel(reach, ctr, visit, demo, deal)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Est. clicks", f"{int(funnel['est_clicks']):,}")
    m2.metric("Est. site visits", f"{int(funnel['est_visits']):,}")
    m3.metric("Est. demo requests", f"{int(funnel['est_demos']):,}")
    m4.metric("Est. pipeline value", f"${int(funnel['est_pipeline_value']):,}")
    st.caption("Plug in the client's real GA4 / CRM rates and these estimates become exact.")
