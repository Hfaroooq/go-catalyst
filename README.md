# Catalyst Content Analytics Tracker

A self-sustaining content-analytics system that, on a schedule and with no manual trigger, pulls
content performance from YouTube, learns what's working, recommends what to make next (and sharpens
those recommendations as new data arrives), and proves ROI for a B2B SaaS client — all surfaced on a
live dashboard.

**Live dashboard:** https://go-catalyst-dana3bjfsffvud2cmozreh.streamlit.app/
**Repo:** https://github.com/Hfaroooq/go-catalyst

- 📄 Memo (architecture, data model, scale, cost): [`docs/MEMO.md`](docs/MEMO.md)
- 🧱 Design spec: [`docs/superpowers/specs/2026-06-23-catalyst-tracker-design.md`](docs/superpowers/specs/2026-06-23-catalyst-tracker-design.md)
- 📘 plain walkthrough of every part: [`docs/docs.md`](docs/docs.md)

## What it does

```
                 ┌─────────────────────────────────────────────┐
   YouTube  ───▶ │  WORKER (GitHub Actions cron, no humans)     │
   Data API      │  pull → classify → analyze → recommend       │
                 │  (+ score last round) → ROI → log            │
                 └───────────────────────┬─────────────────────┘
                                         │ writes
                                  ┌──────▼───────┐
                                  │  Postgres    │  history, not a snapshot
                                  │  (Supabase)  │
                                  └──────┬───────┘
                                         │ reads
                                  ┌──────▼───────┐
                                  │  Streamlit   │  performance · ideas · ROI
                                  └──────────────┘
```

The **worker** thinks; the **dashboard** only displays. They share one Postgres database and never
block each other.

## Tech stack

Python 3.12 (managed by [`uv`](https://docs.astral.sh/uv/)) · YouTube Data API v3 (`httpx`) ·
Supabase Postgres · SQLAlchemy 2.0 + Alembic (real migrations) · Google Gemini (free tier, for the
fuzzy "angle" tag + idea generation) · Streamlit · GitHub Actions (hourly cron).

## Repo layout

```
src/catalyst/
├── config.py            # all secrets via env; fails loudly if one is missing
├── db/        models.py (10 tables), session.py
├── ingest/    base.py (platform interface), youtube.py, pipeline.py (dedup + snapshots)
├── analysis/  metrics.py (engagement-rate aggregations, client-vs-field)
├── recommend/ classify.py (heuristics + Gemini), gemini.py, engine.py (the learning loop)
├── roi/       funnel.py (tunable funnel)
└── jobs/      run_cycle.py (the scheduled entrypoint)
dashboard/app.py         # Streamlit
migrations/              # Alembic migration history
.github/workflows/pull.yml  # the cron
tests/                   # 28 tests
```

## Run it locally

```bash
# 1. Install uv: https://docs.astral.sh/uv/getting-started/installation/
# 2. Install deps (also fetches Python 3.12):
uv sync

# 3. Configure secrets:
cp .env.example .env      # then fill in DATABASE_URL, YOUTUBE_API_KEY, GEMINI_API_KEY,
                          # YOUTUBE_CHANNELS, CLIENT_CHANNEL, CLIENT_DOMAIN

# 4. Apply the database schema:
uv run alembic upgrade head

# 5. Run one full cycle (pull → classify → recommend → ROI):
uv run python -m catalyst.jobs.run_cycle

# 6. Run the dashboard:
uv run streamlit run dashboard/app.py

# Tests:
uv run pytest
```

## How the schedule works

`.github/workflows/pull.yml` runs `run_cycle.py` **hourly** (and on a manual "Run workflow" button)
on GitHub's servers, with secrets stored as GitHub Actions secrets. Every run is recorded in the
`job_runs` table. No always-on server.

## Changing the tracked client

The client and channels are pure config — set `YOUTUBE_CHANNELS` (channel IDs) and `CLIENT_CHANNEL`
in `.env` (locally) and in the GitHub Actions + Streamlit secrets, then re-run the cycle. Nothing in
the code is hardcoded to a specific company.

## Scale & cost

See [`docs/MEMO.md`](docs/MEMO.md) for the data model, indexes, the 50M-row plan (partition the
snapshot table by month, BRIN index on time, daily rollups, archive cold data), and where cost
breaks first.
