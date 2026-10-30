"""Publish aggregate progress, never latent scenarios or held-out identities."""

import json
import sqlite3
from datetime import datetime, timezone

from tracemind.benchmark.builder import BenchmarkBuilder, read, write
from tracemind.config import LOCAL, ROOT, dsn
from tracemind.jobs import JobQueue

queue = JobQueue(dsn())
gemini_root = LOCAL / "datasets/gemini_v1"
terra = BenchmarkBuilder(LOCAL / "datasets/v2", queue).audit()
data = BenchmarkBuilder(gemini_root, queue).audit() if gemini_root.exists() else terra
engineering = BenchmarkBuilder(LOCAL / "datasets/v1", queue).audit()
batch_path = LOCAL / "api/batches/initial-gemini-20261030/state.json"
batch = read(batch_path) if batch_path.exists() else {}
api_calls = None
ledger = LOCAL / "api/requests.sqlite3"
if ledger.exists() and batch:
    tasks = set(batch.get("completed", []) + batch.get("attempted_jobs", []))
    tasks.add("models:" + batch["batch_id"])
    with sqlite3.connect(ledger) as connection:
        api_calls = sum(row[0] in tasks for row in connection.execute("SELECT task FROM attempts"))
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
    "active_synthesis": {
        "model": "gemini-3.8-flash",
        "namespace": "gemini_v1",
        "batch_limit": 20,
        "status": batch.get("status", "not_started"),
        "reason": batch.get("reason"),
        "accepted_records_in_first_batch": len(batch.get("completed", [])),
        "api_requests_in_first_batch": api_calls,
        "next_batch": "requires_explicit_user_request",
    },
    "retained_terra_v2_not_mixed_with_gemini": terra,
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
