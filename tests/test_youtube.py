"""Unit tests for YouTube normalization (pure functions, no network)."""

from __future__ import annotations

from catalyst.ingest.youtube import (
    classify_format,
    metric_from_item,
    normalize_video,
    parse_duration,
    primary_domain,
)

SAMPLE_VIDEO = {
    "id": "abc123",
    "snippet": {
        "publishedAt": "2026-06-20T14:30:00Z",
        "channelId": "UCxyz",
        "channelTitle": "Acme SaaS",
        "title": "How we cut SaaS churn 30% (pricing teardown)",
        "description": "Full write-up: https://www.acme.com/blog/churn and more.",
        "thumbnails": {
            "default": {"url": "https://i.ytimg.com/vi/abc123/default.jpg"},
            "high": {"url": "https://i.ytimg.com/vi/abc123/hqdefault.jpg"},
            "maxres": {"url": "https://i.ytimg.com/vi/abc123/maxresdefault.jpg"},
        },
        "liveBroadcastContent": "none",
        "tags": ["saas", "churn", "pricing"],
    },
    "contentDetails": {"duration": "PT12M34S"},
    "statistics": {"viewCount": "15000", "likeCount": "640", "commentCount": "58"},
}


def test_parse_duration() -> None:
    assert parse_duration("PT1H2M3S") == 3723
    assert parse_duration("PT45S") == 45
    assert parse_duration("PT12M") == 720
    assert parse_duration("PT12M34S") == 754
    assert parse_duration(None) is None
    assert parse_duration("P0D") is None  # live broadcasts have no duration


def test_classify_format() -> None:
    assert classify_format(45, "none") == "short"
    assert classify_format(754, "none") == "long"
    assert classify_format(None, "live") == "livestream"
    assert classify_format(20, "upcoming") == "livestream"


def test_primary_domain() -> None:
    assert primary_domain("see https://www.acme.com/blog/x") == "acme.com"
    assert primary_domain("http://sub.example.org/path?q=1") == "sub.example.org"
    assert primary_domain("no links here") is None
    assert primary_domain(None) is None


def test_normalize_video() -> None:
    post = normalize_video(SAMPLE_VIDEO)
    assert post.external_id == "abc123"
    assert post.title.startswith("How we cut SaaS churn")
    assert post.body is not None and post.body.startswith("Full write-up")
    assert post.post_type == "long"  # 754s > 60s
    assert post.duration_seconds == 754
    assert post.domain == "acme.com"
    assert post.author == "Acme SaaS"
    assert post.thumbnail_url.endswith("maxresdefault.jpg")  # highest available
    assert post.posted_at.year == 2026
    assert post.url == "https://www.youtube.com/watch?v=abc123"


def test_metric_from_item() -> None:
    reading = metric_from_item(SAMPLE_VIDEO)
    assert reading.score == 640  # likes
    assert reading.num_comments == 58
    assert reading.view_count == 15000


def test_metric_handles_missing_stats() -> None:
    # Likes/comments can be disabled on a video.
    reading = metric_from_item({"id": "x", "statistics": {"viewCount": "10"}})
    assert reading.score == 0
    assert reading.num_comments == 0
    assert reading.view_count == 10
