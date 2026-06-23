"""The scheduled entrypoint — one full self-sustaining cycle.

Runs end-to-end with no human:
    ingest -> classify -> recommend (+ score last round) -> ROI

Each step manages its own database transaction and is idempotent, so re-running
is safe. Invoked locally as ``uv run python -m catalyst.jobs.run_cycle`` and on a
schedule by ``.github/workflows/pull.yml``.
"""

from __future__ import annotations

import sys

from catalyst.ingest.pipeline import ingest_once
from catalyst.recommend.classify import classify_once
from catalyst.recommend.engine import recommend_once
from catalyst.roi.funnel import roi_once


def main() -> int:
    print(">> ingest", flush=True)
    print("   ", ingest_once(), flush=True)

    print(">> classify", flush=True)
    print("   ", {"classified": classify_once()}, flush=True)

    print(">> recommend (+ score previous round)", flush=True)
    print("   ", recommend_once(), flush=True)

    print(">> roi", flush=True)
    print("   ", roi_once(), flush=True)

    print(">> cycle complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
