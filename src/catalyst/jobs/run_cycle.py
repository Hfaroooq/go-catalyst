"""The scheduled entrypoint — one full self-sustaining cycle.

Runs end-to-end with no human:
    ingest -> classify -> recommend (+ score last round) -> ROI

**Resilience:** ingest is the core step (if it fails, the run fails). The downstream steps —
classify, recommend, ROI — are best-effort: a transient failure (e.g. a free-tier LLM 503) is
logged but does NOT fail the whole cron, because the next run will pick it up and no data is lost.

Invoked locally as ``uv run python -m catalyst.jobs.run_cycle`` and on a schedule by
``.github/workflows/pull.yml``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from catalyst.ingest.pipeline import ingest_once
from catalyst.recommend.classify import classify_once
from catalyst.recommend.engine import recommend_once
from catalyst.roi.funnel import roi_once


def _step(name: str, fn: Callable[[], object]) -> bool:
    """Run one step; return True on success. Logs and swallows errors (non-fatal)."""
    print(f">> {name}", flush=True)
    try:
        print("   ", fn(), flush=True)
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort step
        print(f"   {name} FAILED (non-fatal, will retry next cycle): {exc}", flush=True)
        return False


def main() -> int:
    # Ingest is the core step — if we can't pull, the run genuinely failed.
    if not _step("ingest", ingest_once):
        print(">> aborting: ingest failed", flush=True)
        return 1

    # Downstream steps are best-effort; a transient blip shouldn't red the whole cron.
    _step("classify", lambda: {"classified": classify_once()})
    _step("recommend (+ score previous round)", recommend_once)
    _step("roi", roi_once)

    print(">> cycle complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
