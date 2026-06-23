"""Classify posts into topic / hook / angle / format.

Hybrid by design (a deliberate build-vs-buy call):
- **format** comes free from the video's duration (already on the post),
- **hook** and **topic** are cheap local heuristics (titles/descriptions are
  formulaic enough for rules + a keyword taxonomy),
- **angle** — the genuinely fuzzy "strategic take" — is the only field we send
  to the LLM (Gemini, free tier), in batches.

Classification is idempotent and versioned: each post is tagged once per
``CLASSIFIER_VERSION``, so re-runs only touch new posts, and we can upgrade the
method later without losing history.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from catalyst.db.models import Post, PostClassification
from catalyst.recommend.gemini import GeminiClient

CLASSIFIER_VERSION = "v1-heuristic+gemini-angle"

_LISTICLE_RE = re.compile(
    r"\b\d+\s+(ways|tips|tools|reasons|steps|things|mistakes|ideas|"
    r"strategies|examples|hacks|lessons|trends|secrets|tactics)\b"
)
_QUESTION_START_RE = re.compile(r"^(why|what|which|when|where|should|is|are|can|do|does|how)\b")

ANGLE_OPTIONS = [
    "beginner-education",
    "advanced-tactics",
    "thought-leadership",
    "product-led",
    "data-driven",
    "contrarian",
    "storytelling",
    "news-update",
    "entertainment",
]

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "SEO": ["seo", "search engine", "backlink", "keyword", "ranking", "serp"],
    "Paid Ads": ["ppc", "google ads", "facebook ads", "paid ads", "ad campaign", "advertising"],
    "Content Marketing": ["content marketing", "blog", "blogging", "copywriting", "content strategy"],
    "Social Media": ["social media", "instagram", "tiktok", "linkedin", "reels", "short-form"],
    "Email Marketing": ["email marketing", "newsletter", "cold email", "email list", "drip"],
    "AI & Automation": ["ai ", "artificial intelligence", "chatgpt", "claude", "gpt", "automation", "llm", "agent", "gemini", "copilot"],
    "Sales": ["sales", "selling", "cold call", "outbound", "prospecting", "crm", "close deals"],
    "Pricing & Monetization": ["pricing", "monetization", "revenue", "mrr", "arr", "subscription"],
    "Product & Demo": ["demo", "walkthrough", "tutorial", "how to use", "getting started", "onboarding", "feature"],
    "Analytics & Data": ["analytics", "metrics", "dashboard", "attribution", "reporting", "data-driven"],
    "Startup & Growth": ["startup", "founder", "growth", "fundraising", "bootstrap", "saas", "go-to-market", "gtm", "scale"],
    "Productivity & Tools": ["productivity", "workflow", "notion", "template", "tool stack"],
    "Branding": ["brand", "branding", "positioning", "messaging"],
}


# --------------------------------------------------------------------------- #
# Heuristics (free, no network)
# --------------------------------------------------------------------------- #
def classify_hook(title: str) -> str:
    """Map a title to a hook archetype using ordered pattern rules."""
    t = (title or "").lower().strip()
    if any(w in t for w in ["how we", "how i ", "case study", "lessons from", "we grew", "my journey"]):
        return "case-study"
    if "how to" in t:
        return "how-to"
    if _LISTICLE_RE.search(t):
        return "listicle"
    if " vs " in t or " versus " in t or "alternative" in t or "compared to" in t:
        return "comparison"
    if any(w in t for w in ["stop ", "don't", "dont ", "mistake", "avoid", "worst", "wrong", "never "]):
        return "contrarian"
    if any(w in t for w in ["announcing", "introducing", "new ", "launch", "now available", "update"]):
        return "announcement"
    if any(w in t for w in ["secret", "nobody tells", "truth about", "hidden", "the real reason"]):
        return "curiosity"
    if t.endswith("?") or _QUESTION_START_RE.match(t):
        return "question"
    return "informational"


def classify_topic(title: str, description: str | None, tags: list[str] | None) -> str:
    """Score the text against a niche keyword taxonomy; pick the best bucket."""
    text = " ".join(
        [title or "", (description or "")[:500], " ".join(tags or [])]
    ).lower()
    best_topic, best_score = "Other", 0
    for topic, keywords in TOPIC_KEYWORDS.items():
        score = sum(text.count(kw) for kw in keywords)
        if score > best_score:
            best_topic, best_score = topic, score
    return best_topic


def _tags_of(post: Post) -> list[str]:
    raw = post.raw or {}
    return raw.get("snippet", {}).get("tags", []) or []


# --------------------------------------------------------------------------- #
# Angle (the one fuzzy field -> Gemini)
# --------------------------------------------------------------------------- #
def build_angle_prompt(videos: list[tuple[str, str, str]]) -> str:
    items = [
        {"id": vid, "title": title, "description": (desc or "")[:300]}
        for vid, title, desc in videos
    ]
    return (
        "You classify the strategic ANGLE of B2B/SaaS YouTube videos.\n"
        "Angle = the approach/take, NOT the topic. Choose exactly one of:\n"
        f"{', '.join(ANGLE_OPTIONS)}.\n\n"
        'Return a JSON array of objects {"id": <id>, "angle": <one option>} for EVERY input.\n\n'
        f"Videos:\n{json.dumps(items, ensure_ascii=False)}"
    )


def classify_angles(client: GeminiClient, videos: list[tuple[str, str, str]]) -> dict[str, str]:
    """Ask Gemini for the angle of each video; returns {id: angle}."""
    if not videos:
        return {}
    result = client.generate_json(build_angle_prompt(videos))
    angles: dict[str, str] = {}
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict) and item.get("id") is not None:
                angle = item.get("angle")
                angles[str(item["id"])] = angle if angle in ANGLE_OPTIONS else "other"
    return angles


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _unclassified_posts(session: Session) -> list[Post]:
    classified = select(PostClassification.post_id).where(
        PostClassification.classifier_version == CLASSIFIER_VERSION
    )
    return list(session.scalars(select(Post).where(Post.id.not_in(classified))))


def run_classification(
    session: Session,
    client: GeminiClient | None = None,
    batch_size: int = 12,
) -> int:
    """Classify every not-yet-classified post; returns the number classified."""
    client = client or GeminiClient.from_settings()
    posts = _unclassified_posts(session)
    if not posts:
        return 0

    classified = 0
    for start in range(0, len(posts), batch_size):
        batch = posts[start : start + batch_size]
        videos = [(str(p.id), p.title, p.body or "") for p in batch]
        angles = classify_angles(client, videos)
        for post in batch:
            session.add(
                PostClassification(
                    post_id=post.id,
                    topic=classify_topic(post.title, post.body, _tags_of(post)),
                    hook_type=classify_hook(post.title),
                    angle=angles.get(str(post.id), "other"),
                    format=post.post_type,
                    classifier_version=CLASSIFIER_VERSION,
                )
            )
            classified += 1
        session.flush()
    return classified


def classify_once(client: GeminiClient | None = None) -> int:
    """Production entry point: open a session and classify new posts."""
    from catalyst.db.session import session_scope

    with session_scope() as session:
        return run_classification(session, client)
