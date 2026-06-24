# Catalyst Content Analytics Tracker — Memo

**Live dashboard:** https://go-catalyst-dana3bjfsffvud2cmozreh.streamlit.app/ ·
**Repo:** https://github.com/Hfaroooq/go-catalyst

A self-sustaining system that pulls YouTube content performance on a schedule, learns what works,
recommends what to make next (and sharpens as data arrives), and proves ROI for a B2B SaaS client.

## Architecture & data model

Three decoupled parts over one Postgres database: a **worker** (GitHub Actions cron) that pulls,
classifies, analyzes, recommends, and computes ROI; the **database** (Supabase Postgres) that stores
*history, not snapshots*; and a read-only **Streamlit dashboard**. Separating the thing that *thinks*
from the thing that *displays* means the dashboard can never slow or break the pipeline.

The schema (10 tables, all created via **Alembic migrations** — versioned and reversible, verified
with a clean downgrade/upgrade) is built around a time-series core:

- **`posts`** — one row per video. Deduped by a `UNIQUE(platform_id, external_id)` key, so re-pulls
  update rather than duplicate. Indexed on `(source_id, posted_at)` and `(is_client, posted_at)`.
- **`metric_snapshots`** ⭐ — the firehose: one row each cycle per post (`score`/likes,
  `num_comments`, `view_count`). Indexed `(post_id, captured_at)` and `(captured_at)`. This is what
  makes it a *tracker* — we re-measure the same videos over time.
- **`post_classifications`** — LLM/heuristic tags (topic, hook, angle, format), versioned so the
  method can improve without losing history.
- **`attribute_performance`** — the **feedback layer**: realized engagement per attribute, with a
  trend. This is the persisted memory that makes the recommendation loop learn.
- **`recommendations`**, **`funnel_assumptions`** + **`funnel_snapshots`** (ROI over time), and
  **`job_runs`** (liveness/debug log).

**Multi-platform by design:** a `platforms`/`sources` layer plus a `PlatformAdapter` interface means
adding a platform is one new file, not a rewrite.

**At 50M rows** the snapshot table is the pressure point. The plan: **range-partition
`metric_snapshots` by month** (small per-partition indexes, cheap archival), add a **BRIN index on
`captured_at`** (tiny, ideal for append-only time data), maintain a **daily rollup table** so the
dashboard reads aggregates not raw rows, **archive cold months** to Parquet/object storage, and use
Supabase's **PgBouncer** pooling. Reads stay fast because the dashboard never scans raw history.

## Platform choice

**YouTube Data API v3.** It was chosen after a deliberate build-vs-buy/access review: **Reddit**
closed *self-service* API access (Nov 2025 — manual approval now required), and **X** removed its
free tier (pay-per-use). YouTube is free, key-only (no approval, no billing), explicitly on the
brief's list, and gives rich metrics that genuinely change over time (views/likes/comments) plus a
real *format* signal (Shorts vs long-form via duration). The client is modelled as a B2B SaaS
channel (Arrivy, a field-service ops platform, in the demo), benchmarked against the field
(ServiceTitan, Jobber, Housecall Pro). Because the system is platform-agnostic, swapping the
client/channels is a config change.

## Analysis, recommendations, and the learning loop

Analysis builds a per-post **performance view** (each post's latest snapshot + tags) and ranks
attributes by **engagement rate = (likes + comments) ÷ views** rather than raw views — so a small
client is compared *fairly* to big channels, measuring resonance, not just reach. It surfaces top
topics/hooks/formats/angles, best posting day, and the client-vs-field gap (e.g. the demo shows the
client — a small challenger — trailing the field on engagement across formats, ~3.5 vs ~26 per 1k on
long-form — an actionable target).

The recommendation loop runs each cycle: (1) recompute realized engagement per attribute into
`attribute_performance` with a trend; (2) **score the previous cycle's ideas against reality** — did
the attributes they bet on remain winners? → a hit-rate (we score the attribute *hypothesis* against
the ongoing stream of videos, not whether the client posted the exact idea); (3) **Gemini generates
new ideas** grounded in proven winners, the client gap, and the prior hit-rate, each with reasoning,
confidence, and a predicted engagement. **It's a real loop because `attribute_performance` is
persisted, outcome-derived state fed back into the next round** — delete it and the system stops
learning rather than just regenerating.

**Build-vs-buy in classification:** structured tags (format from duration, hook from title patterns,
topic from a keyword taxonomy) are done with cheap local code; only the genuinely fuzzy **angle** is
sent to an LLM, in batches. We don't pay a model to tag every row for the parts rules handle well.

## ROI

We can't see the client's CRM, so we model an **explicit, tunable funnel**: real client **views** →
clicks (×CTR) → visits (×visit-rate) → demo requests (×demo-rate) → pipeline $ (×deal value). A
client trusts it because **the top of the funnel is their real, measured reach**, and every
conversion rate is a **visible, editable assumption** (sliders on the dashboard), not a black box —
"plug in your real GA4/CRM rates and it becomes exact." Assumptions are versioned in the database.

## Cost at scale, and where it breaks first

At this size everything is free (YouTube quota, Supabase, Streamlit, public-repo Actions, Gemini free
tier). **First cost as it grows: LLM classification**, which scales with new-video volume — mitigated
because heuristics already do the bulk per-row work, classification is once-per-video and versioned,
the angle call is batched, and at high volume we'd move angle to a cheap local embeddings classifier
too. **First *engineering* break point: the `metric_snapshots` write/scan volume** — addressed by the
partitioning + rollup plan above. YouTube quota (10k units/day) is ample because channels resolve by
ID (1 unit); the only expensive call is the search fallback, used rarely.

**Verified:** 29 tests pass; the full cycle runs end-to-end locally and **in the cloud** (a GitHub
Actions run wrote a fresh `job_runs` row + recommendation cycle); the dashboard renders against live
data (checked with Streamlit's AppTest).
