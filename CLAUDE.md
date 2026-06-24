# CLAUDE.md

Guidance for AI agents (and humans) working in this repo. This file also documents **how the
project was built with AI** — the challenge asks to see how the AI tools were driven.

## What this is

A self-sustaining content-analytics tracker: on a schedule it pulls YouTube content performance,
classifies and analyzes it, recommends what to make next (and scores its own last round so it
sharpens), and proves ROI for a B2B SaaS client. Three decoupled parts over one Postgres DB —
a **worker** (the brain), the **database** (history, not snapshots), and a read-only **Streamlit
dashboard** (the face).

## Where things live

```
src/catalyst/
  config.py      # ALL config/secrets load here (pydantic-settings). Nothing else reads os.environ.
  db/            # models.py = 10 SQLAlchemy tables; session.py = engine + session_scope()
  ingest/        # base.py = PlatformAdapter interface; youtube.py = adapter; pipeline.py = dedup+snapshot
  analysis/      # metrics.py = engagement-rate aggregations, client-vs-field
  recommend/     # classify.py (heuristics + Gemini angle); gemini.py (client); engine.py (the learning loop)
  roi/           # funnel.py = tunable funnel
  jobs/          # run_cycle.py = the scheduled entrypoint
dashboard/app.py # Streamlit (reads only)
migrations/      # Alembic; .github/workflows/pull.yml = hourly cron
docs/            # MEMO.md, docs.md (plain), superpowers/ (spec + plan)
```

## Conventions (follow these)

- **Tooling:** `uv` for everything (`uv sync`, `uv run ...`). Python 3.12 pinned in `.python-version`.
- **Secrets:** only ever via `catalyst.config.get_settings()`. Required keys have no default, so the
  app fails loudly at startup. **Never hardcode a secret;** `.env` is gitignored.
- **Schema changes:** real Alembic migrations only — never hand-edit the DB. `uv run alembic
  revision --autogenerate -m "..."` then review before `upgrade head`.
- **Data is time-series:** store history. Posts are deduped on `UNIQUE(platform_id, external_id)`;
  each cycle appends a `metric_snapshots` row.
- **Performance metric:** rank by `engagement_rate = (likes + comments) / views` (fair across
  channel sizes), not raw views.
- **New platform = new adapter:** implement `PlatformAdapter` (`ingest/base.py`); don't touch the
  rest. The client/channels are config (`YOUTUBE_CHANNELS`, `CLIENT_CHANNEL`), never hardcoded.
- **Classification is hybrid (build-vs-buy):** heuristics for structured tags (format/hook/topic),
  Gemini only for the fuzzy `angle`. It's idempotent + versioned (`classifier_version`).
- **The learning loop is real:** `attribute_performance` is persisted, outcome-derived state that is
  *fed back* into the next round's idea generation. Don't reduce it to "re-prompt the LLM."
- **Tests where they earn their keep:** dedup, normalization, funnel math, analysis aggregations,
  loop scoring. DB-backed tests use the rolled-back `db_session` fixture (no pollution).

## Commands

```bash
uv sync                                   # install deps + Python
uv run alembic upgrade head               # apply schema
uv run python -m catalyst.jobs.run_cycle  # one full cycle
uv run streamlit run dashboard/app.py     # dashboard
uv run pytest                             # tests
```

## Gotchas

- YouTube channel handles don't always match the brand name → `youtube.py` falls back to search.
  Prefer channel IDs (`UC...`) in config (cheap to resolve).
- Use Supabase's **Session pooler** connection string (IPv4); the password must be URL-encoded.
- Gemini keys may start with `AQ.` (not just `AIza`) and still be valid as `?key=` API keys.

## How this was built with AI (the workflow)

1. **Brainstorm → spec.** Requirements and design were explored interactively, then written to
   `docs/superpowers/specs/2026-06-23-catalyst-tracker-design.md` (the memo's backbone).
2. **Plan.** A goal-by-goal implementation plan in `docs/superpowers/plans/`.
3. **Execute, one goal at a time.** Each of the 10 goals followed the same loop: write the code +
   tests, **verify live against the real DB/APIs**, update `docs/docs.md` (a plain
   companion + walkthrough notes for the internal owner), then **commit** — giving a real,
   readable commit history.
4. **Catch the AI when it's wrong.** Decisions were pushed on, not accepted blindly — e.g. the
   platform was pivoted from Reddit (self-service API closed Nov 2025) to YouTube, and an
   over-claimed reading of the brief was corrected against the actual text.

When extending this repo: keep that loop — code + test + verify + document + commit, one focused
change at a time.
