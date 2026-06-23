"""Schema-shape tests.

These check the *design decisions* that matter, without needing a live database:
- all expected tables exist,
- posts can't be duplicated (the dedup unique key),
- the time-series table is indexed by post + time,
- the feedback layer has its attribute-combo unique key.

The real "does it work in Postgres" check is applying the migration to Supabase (Goal 2 deliverable).
"""

from __future__ import annotations

from catalyst.db.models import Base

EXPECTED_TABLES = {
    "platforms",
    "sources",
    "posts",
    "post_classifications",
    "metric_snapshots",
    "recommendations",
    "attribute_performance",
    "funnel_assumptions",
    "funnel_snapshots",
    "job_runs",
}


def _unique_column_sets(table) -> set[tuple[str, ...]]:
    out: set[tuple[str, ...]] = set()
    for c in table.constraints:
        if c.__class__.__name__ == "UniqueConstraint":
            out.add(tuple(sorted(col.name for col in c.columns)))
    return out


def _index_column_sets(table) -> set[tuple[str, ...]]:
    return {tuple(col.name for col in idx.columns) for idx in table.indexes}


def test_all_expected_tables_defined() -> None:
    assert EXPECTED_TABLES <= set(Base.metadata.tables)


def test_posts_have_dedup_unique_key() -> None:
    posts = Base.metadata.tables["posts"]
    assert ("external_id", "platform_id") in _unique_column_sets(posts)


def test_metric_snapshots_indexed_by_post_and_time() -> None:
    snapshots = Base.metadata.tables["metric_snapshots"]
    index_sets = _index_column_sets(snapshots)
    assert any("post_id" in cols and "captured_at" in cols for cols in index_sets)


def test_attribute_performance_has_combo_unique_key() -> None:
    table = Base.metadata.tables["attribute_performance"]
    assert ("angle", "format", "hook_type", "platform_id", "topic") in _unique_column_sets(table)
