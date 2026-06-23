# Catalyst Content Analytics Tracker

A self-sustaining content-analytics tracker that pulls Reddit content performance on a schedule,
learns what's working, recommends what to make next, proves ROI for a B2B SaaS client, and shows
it all on a deployed dashboard.

> Built for the Catalyst GTM Engineer challenge. Design spec:
> [`docs/superpowers/specs/2026-06-23-catalyst-tracker-design.md`](docs/superpowers/specs/2026-06-23-catalyst-tracker-design.md).
> plain walkthrough of every part: [`docs/docs.md`](docs/docs.md).

## Status

Under construction — see [`docs/superpowers/plans/2026-06-23-catalyst-tracker.md`](docs/superpowers/plans/2026-06-23-catalyst-tracker.md)
for the goal-by-goal build plan.

## Tech stack

Python 3.12 (managed by [`uv`](https://docs.astral.sh/uv/)) · PRAW · Supabase Postgres ·
SQLAlchemy + Alembic · Claude API · Streamlit · GitHub Actions (cron).

## Local setup

```bash
# 1. Install uv (one time): https://docs.astral.sh/uv/getting-started/installation/
# 2. Install dependencies (also fetches Python 3.12 if needed):
uv sync

# 3. Configure secrets:
cp .env.example .env   # then fill in your values

# 4. Run the tests:
uv run pytest
```

Full setup (database, Reddit/Anthropic keys, deployment) is documented as each part is built.
