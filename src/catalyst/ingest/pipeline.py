"""Ingestion pipeline: turn adapter output into rows in our database.

Responsibilities (the "build" half of build-vs-buy — the adapter handles the API):
- ensure the platform + source rows exist,
- **upsert** posts so the same post is never duplicated (dedup),
- append a fresh **metric snapshot** for each post (the time-series),
- record the run in ``job_runs`` for liveness/debugging.

``run_ingest`` takes an explicit session so it's testable inside a rolled-back
transaction; ``ingest_once`` is the production convenience that opens its own.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from catalyst.config import get_settings
from catalyst.db.models import JobRun, MetricSnapshot, Platform, Post, Source
from catalyst.db.session import session_scope
from catalyst.ingest.base import NormalizedPost, PlatformAdapter
from catalyst.ingest.youtube import YouTubeAdapter


def ensure_platform(session: Session, name: str) -> Platform:
    platform = session.scalar(select(Platform).where(Platform.name == name))
    if platform is None:
        platform = Platform(name=name)
        session.add(platform)
        session.flush()
    return platform


def ensure_source(session: Session, platform: Platform, key: str) -> Source:
    source = session.scalar(
        select(Source).where(
            Source.platform_id == platform.id, Source.external_key == key
        )
    )
    if source is None:
        source = Source(platform_id=platform.id, kind="channel", external_key=key)
        session.add(source)
        session.flush()
    return source


def upsert_post(
    session: Session,
    platform: Platform,
    source: Source,
    np: NormalizedPost,
    *,
    client_channel: str | None,
    client_domain: str | None,
) -> tuple[Post, bool]:
    """Insert the post, or update it if we've seen it before. Returns (post, created)."""
    is_client = (client_channel is not None and source.external_key == client_channel) or (
        client_domain is not None and np.domain == client_domain
    )
    existing = session.scalar(
        select(Post).where(
            Post.platform_id == platform.id, Post.external_id == np.external_id
        )
    )
    if existing is not None:
        existing.title = np.title
        existing.body = np.body
        existing.post_type = np.post_type
        existing.url = np.url
        existing.permalink = np.permalink
        existing.domain = np.domain
        existing.author = np.author
        existing.thumbnail_url = np.thumbnail_url
        existing.duration_seconds = np.duration_seconds
        existing.is_client = is_client
        existing.raw = np.raw
        return existing, False

    post = Post(
        platform_id=platform.id,
        source_id=source.id,
        external_id=np.external_id,
        title=np.title,
        body=np.body,
        post_type=np.post_type,
        url=np.url,
        permalink=np.permalink,
        domain=np.domain,
        author=np.author,
        thumbnail_url=np.thumbnail_url,
        duration_seconds=np.duration_seconds,
        is_client=is_client,
        posted_at=np.posted_at,
        raw=np.raw,
    )
    session.add(post)
    session.flush()
    return post, True


def run_ingest(
    adapter: PlatformAdapter,
    session: Session,
    channels: list[str],
    *,
    client_channel: str | None = None,
    client_domain: str | None = None,
) -> dict[str, int]:
    """Pull every channel, dedup-upsert posts, snapshot metrics, log the run."""
    platform = ensure_platform(session, adapter.platform_name)
    job = JobRun(status="running")
    session.add(job)
    session.flush()

    posts_seen = posts_new = snapshots_taken = 0
    try:
        for key in channels:
            source = ensure_source(session, platform, key)
            posts = adapter.fetch_posts(key)
            posts_seen += len(posts)

            post_by_ext: dict[str, Post] = {}
            for np in posts:
                post, created = upsert_post(
                    session,
                    platform,
                    source,
                    np,
                    client_channel=client_channel,
                    client_domain=client_domain,
                )
                posts_new += int(created)
                post_by_ext[np.external_id] = post

            readings = adapter.fetch_metrics(list(post_by_ext.keys()))
            for ext_id, reading in readings.items():
                post = post_by_ext.get(ext_id)
                if post is None:
                    continue
                session.add(
                    MetricSnapshot(
                        post_id=post.id,
                        score=reading.score,
                        num_comments=reading.num_comments,
                        upvote_ratio=reading.upvote_ratio,
                        awards=reading.awards,
                        view_count=reading.view_count,
                    )
                )
                snapshots_taken += 1
    except Exception as exc:  # noqa: BLE001 — record the failure, then re-raise
        job.status = "error"
        job.error = str(exc)[:1000]
        job.finished_at = datetime.now(timezone.utc)
        session.flush()
        raise

    job.status = "success"
    job.posts_seen = posts_seen
    job.posts_new = posts_new
    job.snapshots_taken = snapshots_taken
    job.finished_at = datetime.now(timezone.utc)
    session.flush()
    return {
        "posts_seen": posts_seen,
        "posts_new": posts_new,
        "snapshots_taken": snapshots_taken,
    }


def ingest_once(adapter: PlatformAdapter | None = None) -> dict[str, int]:
    """Production entry point: open a session and run one ingest cycle."""
    settings = get_settings()
    adapter = adapter or YouTubeAdapter.from_settings()
    with session_scope() as session:
        return run_ingest(
            adapter,
            session,
            settings.channel_list,
            client_channel=settings.client_channel,
            client_domain=settings.client_domain,
        )
