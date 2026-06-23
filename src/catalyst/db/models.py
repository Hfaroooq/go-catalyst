"""SQLAlchemy models — the database schema.

Design principles (see the spec for the full rationale):
- **Store history, not snapshots.** ``MetricSnapshot`` records a post's metrics every cycle, so
  we can track how content performs *over time*, not just right now.
- **Never duplicate a post.** ``Post`` is unique on ``(platform_id, external_id)`` so re-pulling
  the same post updates it instead of inserting a copy.
- **Multi-platform from day one.** ``Platform``/``Source`` mean adding YouTube later is data, not
  a rewrite.
- **A feedback layer.** ``AttributePerformance`` is the persisted track record that makes the
  recommendation loop learn.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class all models inherit from; carries the shared metadata."""


def _now() -> Mapped[datetime]:
    """A timezone-aware 'created/updated at' column defaulted by the database."""
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- #
# What we track
# --------------------------------------------------------------------------- #
class Platform(Base):
    """A content platform we can pull from (reddit, youtube, ...)."""

    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    created_at: Mapped[datetime] = _now()

    sources: Mapped[list["Source"]] = relationship(back_populates="platform")
    posts: Mapped[list["Post"]] = relationship(back_populates="platform")


class Source(Base):
    """A specific tracked feed on a platform — e.g. the subreddit 'r/SaaS'."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))  # subreddit | channel | author
    external_key: Mapped[str] = mapped_column(String(255))  # e.g. "SaaS"
    display_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    config: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()

    platform: Mapped[Platform] = relationship(back_populates="sources")
    posts: Mapped[list["Post"]] = relationship(back_populates="source")

    __table_args__ = (
        UniqueConstraint("platform_id", "external_key", name="uq_source_platform_key"),
    )


# --------------------------------------------------------------------------- #
# The content + its history
# --------------------------------------------------------------------------- #
class Post(Base):
    """One piece of content (a Reddit submission)."""

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id", ondelete="CASCADE"))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(32))  # Reddit's post id
    title: Mapped[str] = mapped_column(Text)  # the "hook"
    body: Mapped[str | None] = mapped_column(Text)  # selftext
    post_type: Mapped[str] = mapped_column(String(20))  # text | link | image | video
    url: Mapped[str | None] = mapped_column(Text)
    permalink: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(255))
    author: Mapped[str | None] = mapped_column(String(255))
    is_client: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = _now()
    raw: Mapped[dict | None] = mapped_column(JSONB)  # original payload, for safety

    platform: Mapped[Platform] = relationship(back_populates="posts")
    source: Mapped[Source] = relationship(back_populates="posts")
    classifications: Mapped[list["PostClassification"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )
    snapshots: Mapped[list["MetricSnapshot"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("platform_id", "external_id", name="uq_post_platform_external"),
        Index("ix_posts_source_posted", "source_id", "posted_at"),
        Index("ix_posts_client_posted", "is_client", "posted_at"),
    )


class PostClassification(Base):
    """LLM-assigned attributes for a post. Versioned so we can re-classify later."""

    __tablename__ = "post_classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    topic: Mapped[str | None] = mapped_column(String(120))
    hook_type: Mapped[str | None] = mapped_column(String(120))
    angle: Mapped[str | None] = mapped_column(String(120))
    format: Mapped[str | None] = mapped_column(String(60))
    classifier_version: Mapped[str] = mapped_column(String(40))
    classified_at: Mapped[datetime] = _now()

    post: Mapped[Post] = relationship(back_populates="classifications")

    __table_args__ = (
        UniqueConstraint("post_id", "classifier_version", name="uq_classification_post_version"),
    )


class MetricSnapshot(Base):
    """A point-in-time measurement of a post's metrics. The time-series firehose.

    Uses a BIGINT primary key because this is the table that grows fastest
    (posts x snapshots over time).
    """

    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    score: Mapped[int] = mapped_column(Integer)  # upvotes minus downvotes
    num_comments: Mapped[int] = mapped_column(Integer)
    upvote_ratio: Mapped[float | None] = mapped_column(Float)
    awards: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    view_count: Mapped[int | None] = mapped_column(Integer)  # usually null on Reddit

    post: Mapped[Post] = relationship(back_populates="snapshots")

    __table_args__ = (
        Index("ix_snapshots_post_time", "post_id", "captured_at"),
        Index("ix_snapshots_time", "captured_at"),
    )


# --------------------------------------------------------------------------- #
# Recommendations + the learning loop
# --------------------------------------------------------------------------- #
class Recommendation(Base):
    """A content idea the system suggests, with its reasoning and a prediction."""

    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(40))  # groups recs from one run
    created_at: Mapped[datetime] = _now()
    idea_text: Mapped[str] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String(120))
    format: Mapped[str | None] = mapped_column(String(60))
    hook_type: Mapped[str | None] = mapped_column(String(120))
    angle: Mapped[str | None] = mapped_column(String(120))
    reasoning: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    predicted_score: Mapped[float | None] = mapped_column(Float)
    model_version: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'active'"))

    __table_args__ = (
        Index("ix_recommendations_cycle", "cycle_id"),
        Index("ix_recommendations_created", "created_at"),
    )


class AttributePerformance(Base):
    """The feedback layer: realized performance per attribute combo, updated each cycle.

    This is the persisted memory that makes the recommendation loop *learn* — it is read as
    a prior when generating the next round of ideas.
    """

    __tablename__ = "attribute_performance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id", ondelete="CASCADE"))
    topic: Mapped[str] = mapped_column(String(120), server_default=text("''"))
    format: Mapped[str] = mapped_column(String(60), server_default=text("''"))
    hook_type: Mapped[str] = mapped_column(String(120), server_default=text("''"))
    angle: Mapped[str] = mapped_column(String(120), server_default=text("''"))
    avg_score: Mapped[float] = mapped_column(Float, server_default=text("0"))
    sample_size: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    trend: Mapped[float] = mapped_column(Float, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "platform_id", "topic", "format", "hook_type", "angle",
            name="uq_attribute_combo",
        ),
    )


# --------------------------------------------------------------------------- #
# ROI
# --------------------------------------------------------------------------- #
class FunnelAssumptions(Base):
    """Versioned conversion-rate assumptions for the ROI funnel (client-editable)."""

    __tablename__ = "funnel_assumptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    ctr: Mapped[float] = mapped_column(Float)  # reach -> clicks
    visit_rate: Mapped[float] = mapped_column(Float)  # clicks -> site visits
    demo_rate: Mapped[float] = mapped_column(Float)  # visits -> demo requests
    deal_value: Mapped[float] = mapped_column(Float)  # $ per won deal (or per demo)
    effective_from: Mapped[datetime] = _now()
    created_at: Mapped[datetime] = _now()


class FunnelSnapshot(Base):
    """A computed ROI funnel for one cycle, tied to the assumptions used."""

    __tablename__ = "funnel_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(40))
    captured_at: Mapped[datetime] = _now()
    reach: Mapped[float] = mapped_column(Float)
    est_clicks: Mapped[float] = mapped_column(Float)
    est_visits: Mapped[float] = mapped_column(Float)
    est_demos: Mapped[float] = mapped_column(Float)
    est_pipeline_value: Mapped[float] = mapped_column(Float)
    assumptions_id: Mapped[int | None] = mapped_column(
        ForeignKey("funnel_assumptions.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_funnel_snapshots_cycle", "cycle_id"),)


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #
class JobRun(Base):
    """A log row for each scheduled cycle — proves liveness and aids debugging."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'running'"))
    posts_seen: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    posts_new: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    snapshots_taken: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_job_runs_started", "started_at"),)
