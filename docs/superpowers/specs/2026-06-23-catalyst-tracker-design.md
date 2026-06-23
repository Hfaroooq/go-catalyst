# Catalyst Content Analytics Tracker — Design Spec

**Date:** 2026-06-23
**Author:** Haider
**Status:** Approved design — ready for implementation planning

> This document is both the engineering spec and the backbone of the 1–2 page memo
> required by the challenge. plain companion for users: `docs/docs.md`.

> **⚠️ Platform pivot (2026-06-23):** This spec was written for **Reddit**. During the build we
> discovered Reddit closed *self-service* API access (Nov 2025; manual approval now required) and
> X has no free tier, so we pivoted to **YouTube** (free, key-only, and on the brief's list). The
> system is platform-agnostic, so only the ingestion adapter changed. Where this doc says
> "Reddit / subreddit / score / upvote_ratio," read "YouTube / channel / likes / —". Mapping:
> subreddit→channel, post→video, score→likeCount, num_comments→commentCount, view_count→views;
> plus YouTube adds `duration_seconds` (Short vs long-form) and `thumbnail_url`. This section will
> be fully reconciled when the memo is assembled (Goal 10).

---

## 1. Goal & scope

Build a **live content-analytics tracker** that, on a schedule and with no manual trigger,
pulls content performance from a real platform, learns what's working, recommends what to
make next, proves ROI to a client, and keeps a deployed dashboard current.

**Scope decision: lean-but-real.** One platform done well, a real time-series schema with
migrations and indexes, a genuine learning loop, a defensible ROI view, and a self-sustaining
scheduled job — all deployed. No gold-plating. A smaller system that genuinely runs and learns
beats a big one held together with tape.

## 2. System overview

Three parts, one shared database. The core design decision is separating **the thing that
thinks** (worker) from **the thing that displays** (dashboard).

```
                 ┌─────────────────────────────────────────┐
   Reddit API ──▶│  WORKER  (scheduled, no humans)          │
                 │  pull → dedup/store → analyze →           │
                 │  recommend → score last round → ROI       │
                 └───────────────────┬─────────────────────┘
                                     │ writes
                              ┌──────▼───────┐
                              │  Postgres DB │  (history, not a snapshot)
                              └──────┬───────┘
                                     │ reads
                              ┌──────▼───────┐
                              │  DASHBOARD   │  performance · ideas · ROI
                              └──────────────┘
```

## 3. Platform & data source

**Platform: Reddit** (via PRAW, the official-style Python Reddit library).

**Why Reddit:** free API with real OAuth, rich engagement signals (`score`, `num_comments`,
`upvote_ratio`, awards) that genuinely change over time — so the time-series is real, not a
static dump. Reddit is community-first (content is ranked by subreddit communities, not by
follower count), which makes it an honest signal of what an audience actually values.

**The "client" framing (GTM judgment):** We simulate a **B2B SaaS client** doing founder-led
content. We track the communities the client's audience lives in — **r/SaaS, r/startups,
r/Entrepreneur** — and tag each post as either:

- **"client" content** — posts linking to the client's domain and/or by the client's account, OR
- **"field" content** — everything else in the community (the benchmark).

This gives us both halves of the story:
- The **analysis + recommendation engine** learns from the whole community (large sample).
- The **ROI view** focuses on the client's own posts → pipeline.

If the client posts little, the recommender tells them what to post to win in that community
and the ROI model shows projected upside — a clean "onboard a client into a community" story.

**What we pull per post:** id, title (the *hook*), body/selftext, post type (text/link/image/
video), linked domain, author, subreddit, created-at, permalink — plus a repeated **metrics
snapshot** (`score`, `num_comments`, `upvote_ratio`, awards) every cycle to build history.

**Real-world mess we handle:** pagination (PRAW + cursoring), rate limits (PRAW backoff +
our own retry), duplicates (unique key + upsert), missing data (`view_count` is usually null;
nullable columns + defaults), and vote "fuzzing" (we store raw values and treat `score` as
the headline signal, `num_comments` as a second, stronger "provoked a response" signal).

## 4. Tech stack & build-vs-buy

| Job | Choice | Build vs Buy rationale |
|---|---|---|
| Language / deps | Python 3.12 + `uv` | Buy. One fast tool for Python version + deps + venv. |
| Reddit pull | **PRAW** | **Buy** the auth/rate-limit/pagination plumbing. **Build** the dedup, snapshotting, and outcome layer — that's our value. |
| Database | **Supabase** (managed Postgres) | Buy. Real Postgres (legit scale story), free tier, web table browser for the walkthrough. |
| Schema/migrations | **SQLAlchemy 2.0 + Alembic** | Buy the migration engine. Schema lives in versioned code, not a UI. |
| Recommendations | **Claude API** | Buy judgment (idea generation, nuanced angle tagging). See cost note — high-volume per-row tagging would move to a cheap local classifier. |
| Dashboard | **Streamlit** | Buy. Pure-Python dashboard, no JS. UI polish isn't graded. |
| Scheduler | **GitHub Actions cron** | Buy. Free, no always-on server, schedule lives in the repo, manual "Run now" for live demos. |

**Secrets:** env vars locally (`.env`, gitignored), GitHub Actions secrets for the worker,
Streamlit secrets for the dashboard. `.env.example` documents required keys. **No keys in code.**

## 5. Data model (the core)

Principle: **store history, not a single snapshot.** A post's score climbs for ~a day then
plateaus, so we re-measure the same posts over time.

| Table | Purpose | Key columns |
|---|---|---|
| `platforms` | what kind of source | `id`, `name` (reddit/youtube) |
| `sources` | the tracked communities | `id`, `platform_id`, `kind`, `external_key` (subreddit), `is_active` |
| `posts` | one row per post | `id`, `platform_id`, `source_id`, `external_id`, `title`, `body`, `post_type`, `domain`, `author`, `is_client`, `posted_at`, `first_seen_at`. **UNIQUE(`platform_id`, `external_id`)** for dedup |
| `post_classifications` | LLM tags per post | `post_id`, `topic`, `hook_type`, `angle`, `format`, `classifier_version`, `classified_at` |
| `metric_snapshots` ⭐ | the time-series core | `id`, `post_id`, `captured_at`, `score`, `num_comments`, `upvote_ratio`, `awards`, `view_count?` |
| `recommendations` | suggested ideas | `id`, `cycle_id`, `created_at`, `idea_text`, `topic`, `format`, `hook_type`, `angle`, `reasoning`, `confidence`, `predicted_score`, `model_version` |
| `attribute_performance` | **feedback layer** | `id`, `platform_id`, `topic`, `format`, `hook_type`, `angle`, `avg_score`, `sample_size`, `trend`, `updated_at`. UNIQUE on the attribute combo |
| `funnel_assumptions` | ROI knobs (versioned) | `id`, `name`, `ctr`, `visit_rate`, `demo_rate`, `deal_value`, `effective_from` |
| `funnel_snapshots` | ROI over time | `id`, `cycle_id`, `captured_at`, `reach`, `est_clicks`, `est_visits`, `est_demos`, `est_pipeline_value`, `assumptions_id` |
| `job_runs` | run log / liveness | `id`, `started_at`, `finished_at`, `status`, `posts_seen`, `posts_new`, `snapshots_taken`, `error` |

**Relationships:** `sources → posts → {classifications, metric_snapshots}`. `recommendations`
and `attribute_performance` are derived each cycle. `funnel_snapshots → funnel_assumptions`.

**Indexes:**
- `posts`: UNIQUE(`platform_id`,`external_id`); INDEX(`source_id`,`posted_at`); INDEX(`is_client`,`posted_at`)
- `metric_snapshots`: INDEX(`post_id`,`captured_at` DESC); INDEX(`captured_at`)
- `recommendations`: INDEX(`cycle_id`); INDEX(`created_at`)
- `attribute_performance`: UNIQUE(`platform_id`,`topic`,`format`,`hook_type`,`angle`)

## 6. Ingestion pipeline

`ingest/base.py` defines a small **platform-adapter interface** (`fetch_posts`,
`fetch_metrics`). `ingest/reddit.py` implements it with PRAW. `ingest/pipeline.py` orchestrates:

1. **Pull** new + recent posts from each active source (paginated).
2. **Normalize** into our schema (map Reddit fields → `posts` columns; derive `post_type`,
   `domain`, `is_client`).
3. **Upsert** posts on `(platform_id, external_id)` — never duplicate; update mutable fields.
4. **Snapshot** metrics for posts within an active window (e.g. last 7 days) — older posts are
   stable, so we don't re-pull all history every cycle (cost control).
5. **Classify** any unclassified posts via Claude (once per post, batched).
6. Record everything in `job_runs`.

## 7. Analysis layer

`analysis/metrics.py` computes, in SQL where possible:
- **Top hooks / formats / topics** by average peak `score` and `num_comments` (client vs field).
- **Best timing** — day-of-week × hour-of-day performance (Reddit timing matters a lot).
- **Trends over time** — are certain topics/formats rising or fading (using the snapshot history).
- **Client vs field gap** — where the client over/under-performs the community.

## 8. Recommendation engine + learning loop (the heart)

**Approach: hybrid + feedback.** Each scheduled cycle:

1. **Facts (SQL):** compute current winning attributes from `attribute_performance` + latest analysis.
2. **Ideas (Claude):** feed Claude a structured summary of what's winning, the client's gaps,
   and **the track record of past recommendations**; it returns new ideas (topic + format +
   hook + angle + suggested timing) each with **reasoning + confidence + predicted score**.
   Stored in `recommendations`.
3. **Score last round (the loop):** when new snapshot data arrives, measure how content matching
   previous recommendations' attributes actually performed, and update `attribute_performance`
   (rolling average + sample size + trend). Also track the recommender's own hit-rate over time.
4. **Sharpen:** next cycle, step 2 is fed the updated track record, so it doubles down on what's
   working and backs off what isn't. The loop gets sharper as data accumulates — it does not
   merely re-prompt.

**Why this is a real loop:** the feedback (`attribute_performance`) is *persisted state derived
from realized outcomes* and is an *input* to the next generation. Remove it and the system would
just regenerate; with it, predictions measurably improve.

## 9. ROI model

**Approach: explicit, tunable funnel.** Trustworthy because the **top of the funnel is real
measured data** and **every conversion assumption is visible and client-editable**.

```
reach (real: derived from score + comments)
  → est. clicks      (× CTR assumption)
  → est. site visits (× visit rate)
  → est. demo requests (× demo conversion)
  → est. pipeline $  (× average deal value)
```

Assumptions live in `funnel_assumptions` (versioned) and are surfaced on the dashboard as
sliders. The pitch to a client: *"Top-of-funnel is your real Reddit performance. Plug in your
GA4/CRM conversion rates and deal size, and this becomes exact — until then these are explicit,
adjustable estimates, not a black box."* As real click data arrives, early steps move from
assumed to measured.

## 10. Self-sustaining schedule

`jobs/run_cycle.py` is the single entrypoint: pull → analyze → recommend → score → ROI →
log to `job_runs`. Run by **GitHub Actions cron** (e.g. hourly) with a `workflow_dispatch`
"Run now" button for live demos. Secrets via Actions secrets. The workflow file lives in the
repo so graders can see the schedule. `job_runs` proves liveness and is the debugging window.

## 11. Dashboard

Streamlit, three views, all reading the latest DB state:
1. **Performance** — top hooks/formats/topics, timing heatmap, trends, client-vs-field.
2. **Recommendations** — current ideas with reasoning/confidence, and the recommender's
   improving hit-rate over time (shows the loop working).
3. **ROI** — the funnel with editable assumption sliders and pipeline-$ through-line.

## 12. Scale & cost

**The 50M-row question** (the firehose is `metric_snapshots` = posts × snapshots over time):
- **Range-partition `metric_snapshots` by month** → small per-partition indexes, cheap archival/drop.
- **BRIN index on `captured_at`** → tiny, ideal for append-only time-ordered data.
- **Daily rollup table** → dashboard reads aggregates, not raw rows; keep raw for drill-down.
- **Archive cold months** to cheap object storage (Parquet) after N months.
- **Connection pooling** (Supabase PgBouncer) for many dashboard/job connections.

**Cost at scale & where it breaks first:** Reddit/Supabase/Streamlit/Actions are free at this
size. The first real cost is **Claude classification**, which grows with new-post volume. Break
point: per-row LLM tagging at high volume. Mitigation — and a deliberate **build-vs-buy** call:
classify each post once, batch requests, use a cheaper model for tagging, and at high volume
**replace per-row LLM tagging with a local embeddings + rules classifier** to avoid paying an
LLM per row. Idea generation stays on Claude (low volume, high judgment).

## 13. Repo structure

```
go-catalyst/
├── README.md
├── pyproject.toml            # deps (uv)
├── .env.example              # required secrets, no values
├── .gitignore
├── alembic.ini, migrations/  # real migration history
├── src/catalyst/
│   ├── config.py             # loads secrets safely (pydantic-settings)
│   ├── db/        models.py, session.py
│   ├── ingest/    base.py, reddit.py, pipeline.py
│   ├── analysis/  metrics.py
│   ├── recommend/ classify.py, engine.py
│   ├── roi/       funnel.py
│   └── jobs/      run_cycle.py
├── dashboard/app.py
├── tests/
└── .github/workflows/pull.yml
```

## 14. Testing strategy

Tests where they earn their keep, not for coverage's sake:
- **Dedup/upsert** — same post twice → one row, metrics updated.
- **Normalization** — Reddit payload → correct `post_type`/`domain`/`is_client`.
- **Funnel math** — assumptions in → expected pipeline-$ out.
- **Analysis aggregations** — known fixtures → expected top-N.
- **Loop scoring** — recommendation + outcome fixtures → expected `attribute_performance` update.

## 15. Out of scope (YAGNI)

Multi-platform at launch (architecture supports it; only Reddit is built), user accounts/auth on
the dashboard, real-time streaming, a custom JS frontend, and real client CRM integration
(we model the proxy and leave a clear seam to plug it in).

## 16. Build-time decisions to settle

- Exact **client domain** to treat as "ours" (prefer a real SaaS that actually appears in the
  tracked subreddits, so there's live client data, not only projections).
- Cron frequency (hourly is a good default for visible-but-cheap updates).
- Claude model + pricing — to be confirmed against the `claude-api` reference before writing
  that code.
