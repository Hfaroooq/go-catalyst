"""Recommendation engine + the learning loop.

Each cycle:
1. **update_attribute_performance** — recompute realized engagement per attribute value (topic,
   format, hook, angle) from the latest data, and store it in ``attribute_performance`` with a
   ``trend`` vs the previous cycle. This is the system's persisted memory of what works.
2. **score_recommendations** — judge the *previous* cycle's ideas against reality: did the
   attributes they bet on actually turn out to be winners? Produces a hit-rate. (We score the
   attribute *hypothesis* against the ongoing stream of content, not whether the client posted the
   exact idea.)
3. **generate_recommendations** — ask Gemini for new ideas, grounded in the winners, the
   client-vs-field gaps, and the last round's hit-rate.

It's a real loop because step 1's output is persisted state derived from outcomes, and it's an
input to step 3 — so recommendations sharpen as data accumulates.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from catalyst.analysis.metrics import client_vs_field, summarize, top_by_attribute
from catalyst.db.models import AttributePerformance, Platform, Recommendation
from catalyst.recommend.gemini import GeminiClient

MODEL_VERSION = "gemini-2.5-flash"

# Which dimensions we track marginal performance for. Each gets its own
# attribute_performance rows (other dimension columns left as "").
_DIMENSIONS = ("topic", "format", "hook_type", "angle")


# --------------------------------------------------------------------------- #
# Step 1: measure what's working (the memory)
# --------------------------------------------------------------------------- #
def _get_or_create_attr(
    session: Session, platform_id: int, topic: str, fmt: str, hook: str, angle: str
) -> AttributePerformance:
    row = session.scalar(
        select(AttributePerformance).where(
            AttributePerformance.platform_id == platform_id,
            AttributePerformance.topic == topic,
            AttributePerformance.format == fmt,
            AttributePerformance.hook_type == hook,
            AttributePerformance.angle == angle,
        )
    )
    if row is None:
        row = AttributePerformance(
            platform_id=platform_id, topic=topic, format=fmt, hook_type=hook, angle=angle
        )
        session.add(row)
        session.flush()
    return row


def update_attribute_performance(session: Session, platform_id: int) -> int:
    """Recompute marginal engagement per attribute value; track trend vs last cycle."""
    updated = 0
    for dimension in _DIMENSIONS:
        for row in top_by_attribute(session, dimension, limit=100):
            value = row["value"] or ""
            key = {"topic": "", "fmt": "", "hook": "", "angle": ""}
            # map dimension name -> our key arg
            key_name = {"topic": "topic", "format": "fmt", "hook_type": "hook", "angle": "angle"}[dimension]
            key[key_name] = value
            attr = _get_or_create_attr(session, platform_id, key["topic"], key["fmt"], key["hook"], key["angle"])
            new_score = float(row["engagement_per_1k"])
            attr.trend = new_score - float(attr.avg_score or 0)
            attr.avg_score = new_score
            attr.sample_size = int(row["n"])
            updated += 1
    session.flush()
    return updated


def _marginal_rows(session: Session, platform_id: int, dimension: str) -> list[AttributePerformance]:
    """attribute_performance rows for a single dimension's marginal values."""
    col = {"topic": AttributePerformance.topic, "format": AttributePerformance.format,
           "hook_type": AttributePerformance.hook_type, "angle": AttributePerformance.angle}[dimension]
    others = [c for d, c in {
        "topic": AttributePerformance.topic, "format": AttributePerformance.format,
        "hook_type": AttributePerformance.hook_type, "angle": AttributePerformance.angle}.items() if d != dimension]
    return list(
        session.scalars(
            select(AttributePerformance).where(
                AttributePerformance.platform_id == platform_id,
                col != "",
                *[o == "" for o in others],
            )
        )
    )


# --------------------------------------------------------------------------- #
# Step 2: score the previous round (the learning)
# --------------------------------------------------------------------------- #
def score_recommendations(session: Session, platform_id: int, cycle_id: str) -> dict:
    """A rec 'hits' if its recommended topic's realized engagement is at/above average."""
    recs = list(session.scalars(select(Recommendation).where(Recommendation.cycle_id == cycle_id)))
    if not recs:
        return {"scored": 0, "hit_rate": None}

    topic_perf = {r.topic: float(r.avg_score) for r in _marginal_rows(session, platform_id, "topic")}
    avg_all = sum(topic_perf.values()) / len(topic_perf) if topic_perf else 0.0

    hits = 0
    for rec in recs:
        realized = topic_perf.get(rec.topic or "", 0.0)
        is_hit = realized > 0 and realized >= avg_all
        rec.status = "hit" if is_hit else "miss"
        hits += int(is_hit)
    session.flush()
    return {"scored": len(recs), "hit_rate": round(hits / len(recs), 2)}


# --------------------------------------------------------------------------- #
# Step 3: generate new ideas, grounded in the memory
# --------------------------------------------------------------------------- #
def build_recommendation_prompt(summary: dict, winners: list[dict], hit_rate: float | None, n: int) -> str:
    return (
        "You are a GTM content strategist for a B2B SaaS client on YouTube.\n"
        f"Using the performance data below, propose {n} NEW video ideas the client should make next.\n"
        "Favor attributes that are proven winners, and target where the client underperforms the field.\n\n"
        f"Top topics (engagement per 1k views): {json.dumps(summary['top_topics'][:6])}\n"
        f"Top hooks: {json.dumps(summary['top_hooks'][:6])}\n"
        f"Format performance: {json.dumps(summary['top_formats'])}\n"
        f"Top angles: {json.dumps(summary['top_angles'][:6])}\n"
        f"Client vs field by format: {json.dumps(summary['client_vs_field_format'])}\n"
        f"Proven-winner attributes (value, avg engagement/1k, sample, trend): {json.dumps(winners[:15])}\n"
        f"Our recommender's hit-rate on the previous round: {hit_rate}\n\n"
        f"Return a JSON array of exactly {n} objects with keys: "
        '"title", "topic", "format", "hook", "angle", "suggested_day", '
        '"reasoning", "confidence" (0-1 number), "predicted_engagement_per_1k" (number).'
    )


def _winner_summary(session: Session, platform_id: int) -> list[dict]:
    rows: list[AttributePerformance] = []
    for dimension in _DIMENSIONS:
        rows.extend(_marginal_rows(session, platform_id, dimension))
    rows.sort(key=lambda r: float(r.avg_score), reverse=True)
    out = []
    for r in rows:
        value = r.topic or r.format or r.hook_type or r.angle
        out.append({"value": value, "avg_eng_per_1k": round(float(r.avg_score), 2),
                    "n": r.sample_size, "trend": round(float(r.trend), 2)})
    return out


def generate_recommendations(
    session: Session,
    client: GeminiClient,
    platform_id: int,
    cycle_id: str,
    *,
    n: int = 5,
    hit_rate: float | None = None,
) -> int:
    summary = summarize(session)
    winners = _winner_summary(session, platform_id)
    prompt = build_recommendation_prompt(summary, winners, hit_rate, n)
    ideas = client.generate_json(prompt)
    if not isinstance(ideas, list):
        ideas = ideas.get("recommendations", []) if isinstance(ideas, dict) else []

    created = 0
    for idea in ideas:
        if not isinstance(idea, dict):
            continue
        session.add(
            Recommendation(
                cycle_id=cycle_id,
                idea_text=str(idea.get("title", "")),
                topic=idea.get("topic"),
                format=idea.get("format"),
                hook_type=idea.get("hook"),
                angle=idea.get("angle"),
                reasoning=idea.get("reasoning"),
                confidence=_as_float(idea.get("confidence")),
                predicted_score=_as_float(idea.get("predicted_engagement_per_1k")),
                model_version=MODEL_VERSION,
                status="active",
            )
        )
        created += 1
    session.flush()
    return created


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_recommendation_cycle(session: Session, client: GeminiClient | None = None, *, n: int = 5) -> dict:
    client = client or GeminiClient.from_settings()
    platform = session.scalar(select(Platform).where(Platform.name == "youtube"))
    if platform is None:
        raise RuntimeError("no 'youtube' platform — ingest some data first")

    # 1. refresh the memory from the latest data
    update_attribute_performance(session, platform.id)

    # 2. score the previous cycle's recommendations (the learning step)
    prev_cycle = session.scalar(
        select(Recommendation.cycle_id).order_by(Recommendation.created_at.desc()).limit(1)
    )
    scoring = score_recommendations(session, platform.id, prev_cycle) if prev_cycle else {"hit_rate": None}

    # 3. generate new recommendations, grounded in the refreshed memory + hit-rate
    cycle_id = "cycle-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    created = generate_recommendations(
        session, client, platform.id, cycle_id, n=n, hit_rate=scoring.get("hit_rate")
    )
    return {
        "cycle_id": cycle_id,
        "recommendations": created,
        "previous_cycle": prev_cycle,
        "previous_hit_rate": scoring.get("hit_rate"),
    }


def recommend_once(client: GeminiClient | None = None, *, n: int = 5) -> dict:
    from catalyst.db.session import session_scope

    with session_scope() as session:
        return run_recommendation_cycle(session, client, n=n)
