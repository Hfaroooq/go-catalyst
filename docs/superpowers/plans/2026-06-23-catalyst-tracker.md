# Catalyst Content Analytics Tracker — Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan goal-by-goal,
> with a review + walkthrough checkpoint after each goal. Steps use checkbox (`- [ ]`) syntax.
>
> **Note on granularity:** This plan is executed in the same session by the author of the spec and
> reviewed via the *working result* (not by reading code in this plan). So tasks are sized as
> **goals/milestones** with concrete deliverables and verification, rather than pre-written
> line-by-line code. Each goal ends with a visible, testable deliverable.

**Goal:** A self-sustaining Reddit content-analytics tracker that pulls on a schedule, learns
what works, recommends what to make next, proves ROI for a simulated B2B SaaS client, and shows
it all on a deployed dashboard.

**Architecture:** Three parts sharing one Postgres DB — a scheduled **worker** (pull → store →
analyze → recommend → score last round → ROI), the **database** (time-series history), and a
read-only **Streamlit dashboard**. A platform-adapter interface keeps Reddit swappable/extensible.

**Tech Stack:** Python 3.12 (`uv`), PRAW, Supabase Postgres, SQLAlchemy 2.0 + Alembic,
Claude API, Streamlit, GitHub Actions cron.

## Global Constraints

- **No secrets in code.** All keys via env vars / `.env` (gitignored) / GitHub Actions secrets /
  Streamlit secrets. `.env.example` documents required keys with no values.
- **Real migrations only.** Schema changes go through Alembic, never hand-edited in the Supabase UI.
- **Store history, not snapshots.** Metrics are time-series; re-measure the same posts over time.
- **Dedup by `(platform_id, external_id)`** with upsert. Never duplicate a post.
- **Lean-but-real.** Build what's graded well; skip gold-plating. Multi-platform is designed-for,
  not built (Reddit only at launch).
- **Confirm Claude model + pricing** against the `claude-api` reference before writing any code
  that calls Claude (Goals 4 and 6).
- **Tests where they earn their keep:** dedup/upsert, normalization, funnel math, analysis
  aggregations, loop scoring.

## Accounts / keys you (Haider) will create — I'll guide each when we reach it

| Needed at | Account / key | Why |
|---|---|---|
| Goal 2 | **Supabase** project → `DATABASE_URL` | the database |
| Goal 3 | **Reddit** app (client id/secret) | pulling content |
| Goal 4 | **Anthropic** API key | classification + ideas |
| Goal 8 | **GitHub** repo | host code + run the cron |
| Goal 9 | **Streamlit Community Cloud** (connects to GitHub) | deploy the dashboard |

## File structure (locked here)

```
go-catalyst/
├── README.md                       # how to run (Goal 10)
├── pyproject.toml                  # deps, managed by uv (Goal 1)
├── .env.example                    # required secrets, no values (Goal 1)
├── .gitignore                      # done
├── alembic.ini                     # Alembic config (Goal 2)
├── migrations/versions/            # real migration history (Goal 2+)
├── src/catalyst/
│   ├── __init__.py
│   ├── config.py                   # pydantic-settings: load env safely (Goal 1)
│   ├── db/
│   │   ├── models.py               # SQLAlchemy table definitions (Goal 2)
│   │   └── session.py              # engine/session from DATABASE_URL (Goal 2)
│   ├── ingest/
│   │   ├── base.py                 # PlatformAdapter interface (Goal 3)
│   │   ├── reddit.py               # PRAW adapter: posts + metrics (Goal 3)
│   │   └── pipeline.py             # normalize → upsert → snapshot (Goal 3)
│   ├── recommend/
│   │   ├── classify.py             # Claude tags: topic/hook/angle/format (Goal 4)
│   │   └── engine.py               # ideas + score-last-round + sharpen (Goal 6)
│   ├── analysis/
│   │   └── metrics.py              # top hooks/formats/topics, timing, trends (Goal 5)
│   ├── roi/
│   │   └── funnel.py               # funnel math + assumptions (Goal 7)
│   └── jobs/
│       └── run_cycle.py            # the scheduled entrypoint (Goal 8)
├── dashboard/app.py                # Streamlit: performance/ideas/ROI (Goal 9)
├── tests/                          # per-goal tests
└── .github/workflows/pull.yml      # cron worker (Goal 8)
```

---

## Goal 1: Project skeleton + safe config

**What it builds:** the `uv` project, the folder structure above, `config.py` that loads secrets
from env (and fails loudly if a required one is missing), `.env.example`, and a README stub.

**Files:** create `pyproject.toml`, `src/catalyst/__init__.py`, `src/catalyst/config.py`,
`.env.example`, `tests/test_config.py`, plus empty package `__init__.py` files.

**Deliverable / how we verify:** `uv run python -c "import catalyst; print('ok')"` prints `ok`;
`uv run pytest` passes a config test (loading from a fake env works, missing required key raises).

**You'll need:** nothing yet.

**Defend-it note (for the doc):** why `uv`, why config is centralized, why secrets load from env.

- [ ] Initialize `uv` project + pin Python 3.12
- [ ] Create folder/package structure
- [ ] Write `config.py` (pydantic-settings) + `.env.example`
- [ ] Test: config loads from env; missing required key raises
- [ ] Update the docs
- [ ] Commit

## Goal 2: Database foundation (the schema + first real migration)

**What it builds:** all SQLAlchemy models from the spec, the DB session/engine, Alembic wired up,
and the **first migration applied to a live Supabase database**.

**Files:** `src/catalyst/db/models.py`, `src/catalyst/db/session.py`, `alembic.ini`,
`migrations/env.py`, `migrations/versions/0001_*.py`, `tests/test_models.py`.

**Deliverable / how we verify:** the tables (`platforms`, `sources`, `posts`,
`post_classifications`, `metric_snapshots`, `recommendations`, `attribute_performance`,
`funnel_assumptions`, `funnel_snapshots`, `job_runs`) are **visible in the Supabase table
browser**. `alembic upgrade head` runs clean; `alembic downgrade` then `upgrade` round-trips.

**You'll need:** a free **Supabase** project; I'll show you exactly where to copy the
`DATABASE_URL` from.

**Defend-it note:** the time-series design, the unique-key dedup, the indexes, and the
50M-row scaling answer (partitioning + BRIN + rollups).

- [ ] Write models with indexes + unique constraints
- [ ] Wire Alembic; autogenerate + hand-check migration `0001`
- [ ] Apply to Supabase; verify tables + indexes exist
- [ ] Test: models import; a round-trip insert/read works against a test DB
- [ ] Update the docs
- [ ] Commit

## Goal 3: Reddit ingestion (real data flowing in)

**What it builds:** the platform-adapter interface, the PRAW Reddit adapter (fetch recent posts +
their metrics, handling pagination/rate-limits/missing fields), and the pipeline that normalizes,
**dedups/upserts**, and writes a metrics **snapshot**.

**Files:** `src/catalyst/ingest/base.py`, `reddit.py`, `pipeline.py`,
`tests/test_pipeline.py` (dedup + normalization).

**Deliverable / how we verify:** run the puller once → **real r/SaaS+r/startups+r/Entrepreneur
posts and snapshots appear in Supabase**. Running it twice does NOT duplicate posts (dedup test).

**You'll need:** a free **Reddit app** (script type) → client id/secret; I'll walk you through it.

**Defend-it note:** how we handle the real-world mess (Reddit's ~1000 listing cap, rate limits,
duplicates, missing `view_count`), and what's "buy" (PRAW) vs "build" (snapshotting/dedup).

- [ ] Define `PlatformAdapter` interface
- [ ] Implement Reddit adapter (posts + metrics, pagination, backoff)
- [ ] Implement pipeline: normalize → upsert (dedup) → snapshot → log to `job_runs`
- [ ] Test: same post twice = one row, metrics updated; payload → correct fields
- [ ] Run live; confirm rows in Supabase
- [ ] Update the docs
- [ ] Commit

## Goal 4: Classification (give each post its attributes)

**What it builds:** `classify.py` — sends post titles/bodies to Claude and stores `topic`,
`hook_type`, `angle`, `format` in `post_classifications` (once per post, batched, versioned).

**Files:** `src/catalyst/recommend/classify.py`, `tests/test_classify.py` (with a stubbed LLM).

**Deliverable / how we verify:** posts gain classification rows visible in Supabase; re-running
doesn't re-classify already-tagged posts.

**You'll need:** an **Anthropic API key**. (I'll confirm the exact model + price first.)

**Defend-it note:** why Claude for *judgment* tagging now, but a cheap local classifier at scale
(the "don't pay an LLM per row" build-vs-buy point).

- [ ] Confirm Claude model/pricing via `claude-api`
- [ ] Implement classifier (batched, idempotent, versioned)
- [ ] Test: mapping logic with a stubbed LLM response
- [ ] Run live; confirm classifications in DB
- [ ] Update the docs
- [ ] Commit

## Goal 5: Analysis (surface what's working)

**What it builds:** `metrics.py` — top hooks/formats/topics (client vs field), best timing
(day×hour), and trends over time, computed in SQL.

**Files:** `src/catalyst/analysis/metrics.py`, `tests/test_metrics.py` (aggregations on fixtures).

**Deliverable / how we verify:** functions return sensible top-N + timing + trends on the real
data; tests pass on fixed input.

**You'll need:** nothing new.

**Defend-it note:** why these signals, why SQL not Python for aggregation, score vs comments.

- [ ] Implement aggregation queries
- [ ] Test: known fixtures → expected top-N / timing
- [ ] Run on real data; sanity-check output
- [ ] Update the docs
- [ ] Commit

## Goal 6: Recommendations + the learning loop (the heart)

**What it builds:** `engine.py` — (a) generate ideas via Claude fed by analysis + the track
record; (b) **score the previous round's recommendations** against new outcomes; (c) update
`attribute_performance` so next round is sharper.

**Files:** `src/catalyst/recommend/engine.py`, `tests/test_engine.py` (loop scoring on fixtures).

**Deliverable / how we verify:** `recommendations` populated with reasoning/confidence;
`attribute_performance` updates after a second cycle; the recommender's hit-rate is computable.

**You'll need:** nothing new.

**Defend-it note:** why this is a *real* loop (persisted feedback is an input to next round),
not just re-prompting.

- [ ] Implement idea generation (reads analysis + `attribute_performance`)
- [ ] Implement scoring of last round → update `attribute_performance`
- [ ] Test: rec + outcome fixtures → expected attribute update + hit-rate
- [ ] Run two cycles; show sharpening
- [ ] Update the docs
- [ ] Commit

## Goal 7: ROI funnel (prove it to a client)

**What it builds:** `funnel.py` — reach → clicks → visits → demos → pipeline $, driven by
versioned `funnel_assumptions`; writes `funnel_snapshots`.

**Files:** `src/catalyst/roi/funnel.py`, `tests/test_funnel.py` (math).

**Deliverable / how we verify:** ROI numbers in `funnel_snapshots`; funnel-math test passes;
changing an assumption changes the output predictably.

**You'll need:** nothing new.

**Defend-it note:** real top-of-funnel + visible/editable assumptions = client trust.

- [ ] Implement funnel math + assumptions loading
- [ ] Test: assumptions in → expected pipeline $ out
- [ ] Run; write snapshot
- [ ] Update the docs
- [ ] Commit

## Goal 8: The self-sustaining cycle + cron

**What it builds:** `run_cycle.py` (wires pull → classify → analyze → recommend → score → ROI →
log) and `.github/workflows/pull.yml` (cron + a "Run now" button), with secrets in Actions.

**Files:** `src/catalyst/jobs/run_cycle.py`, `.github/workflows/pull.yml`.

**Deliverable / how we verify:** one command runs the whole cycle locally; the GitHub Action runs
it on schedule and on manual trigger; `job_runs` shows each run.

**You'll need:** a **GitHub repo** + adding the secrets to Actions; I'll walk you through it.

**Defend-it note:** what makes it genuinely self-sustaining; how to show liveness in the walkthrough.

- [ ] Implement `run_cycle` entrypoint
- [ ] Write the Actions workflow (cron + workflow_dispatch + secrets)
- [ ] Push; confirm a scheduled + manual run succeeds and writes `job_runs`
- [ ] Update the docs
- [ ] Commit

## Goal 9: Dashboard (deploy it live)

**What it builds:** `dashboard/app.py` — three views (Performance, Recommendations, ROI with
assumption sliders), reading the latest DB state; deployed to Streamlit Community Cloud.

**Files:** `dashboard/app.py`, `.streamlit/` config (secrets NOT committed).

**Deliverable / how we verify:** a **public URL** showing live data; it reflects the latest cycle.

**You'll need:** **Streamlit Community Cloud** (sign in with GitHub, point at the repo).

**Defend-it note:** why read-only dashboard separated from the worker; the three views' purpose.

- [ ] Build the three views against the DB
- [ ] Deploy to Streamlit Community Cloud with DB secret
- [ ] Confirm live URL reflects real data
- [ ] Update the docs
- [ ] Commit

## Goal 10: README, tests green, memo finalized

**What it builds:** a README that lets anyone run it, a final `uv run pytest` pass, and the 1–2
page memo assembled from the spec.

**Files:** `README.md`, `docs/MEMO.md` (or export), final docs pass.

**Deliverable / how we verify:** README steps work from scratch; all tests pass; memo covers
architecture/data model/platforms/analysis+loop/ROI/cost-at-scale.

**You'll need:** nothing new.

- [ ] Write README (setup, env, run worker, run dashboard, run tests)
- [ ] Ensure full test suite passes
- [ ] Assemble memo from spec
- [ ] Final commit

---

## Self-review (against the spec)

**Spec coverage:** Reddit pull (Goal 3 ✓), schema/migrations/indexes (Goal 2 ✓), analysis
(Goal 5 ✓), recommendations + learning loop (Goal 6 ✓), ROI (Goal 7 ✓), self-sustaining schedule
(Goal 8 ✓), deployed dashboard (Goal 9 ✓), scale answer (Goal 2 defend-it + memo ✓), build-vs-buy
(Goals 3/4 defend-it + memo ✓), tests (each goal ✓), README + memo (Goal 10 ✓). No gaps.

**Placeholder scan:** No "TBD/handle edge cases" hand-waves; each goal has concrete deliverable +
verification. Code is written at execution time (same session) per the granularity note.

**Type consistency:** Table/column names match the spec's data-model section; module/file names
match the file-structure map. Adapter interface (Goal 3) is consumed by pipeline + run_cycle.

**Ordering/deps:** 1→2 (config before DB), 2→3 (DB before ingest), 3→4 (data before tagging),
4→5/6 (tags before analysis/recs), 5→6 (analysis feeds recs), all→8 (cycle wires them), 8→9
(data before dashboard). Sound.
