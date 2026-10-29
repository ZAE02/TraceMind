"""Publish aggregate progress, never latent scenarios or held-out identities."""

import json
from datetime import datetime, timezone

from tracemind.benchmark.builder import BenchmarkBuilder, read, write
from tracemind.config import LOCAL, ROOT, dsn
from tracemind.jobs import JobQueue

queue = JobQueue(dsn())
data = BenchmarkBuilder(LOCAL / "datasets/v2", queue).audit()
engineering = BenchmarkBuilder(LOCAL / "datasets/v1", queue).audit()
proof = LOCAL / "verification/stack.json"
human_path = LOCAL / "reviews.sqlite3"
if human_path.exists():
    from tracemind.web.store import ReviewStore

    human_progress = ReviewStore(human_path).statistics()
else:
    human_progress = {"locked_judgments": 0, "paired_packets": 0, "complete": False}
report = {
    "reported_at": datetime.now(timezone.utc).isoformat(),
    "version": "0.2.0",
    "dataset": data,
    "engineering_smoke_not_formal_data": engineering,
    "target": {"groups": 20, "trajectories": 80, "sessions": 480, "checkpoints": 240},
    "stack_verification": read(proof) if proof.exists() else None,
    "human_review": {
        "completed": human_progress["locked_judgments"],
        "actual_pair_packets": human_progress["paired_packets"],
        "target_pair_packets": 200,
        "target_independent_judgments": 400,
        "status": "complete" if human_progress["complete"] else "pending",
    },
    "experiment_result_status": "No result claim until actual candidates and reviews are complete",
}
write(ROOT / "docs/status.json", report)
print(json.dumps(report, ensure_ascii=False, indent=2))
