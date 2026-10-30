"""Publish aggregate progress, never latent scenarios or held-out identities."""

import json
import sqlite3
from datetime import datetime, timezone

from tracemind.api_client import read_policy
from tracemind.benchmark.builder import BenchmarkBuilder, read, write
from tracemind.config import LOCAL, ROOT, dsn
from tracemind.jobs import JobQueue, digest

queue = JobQueue(dsn())
policy, _ = read_policy()
gemini_root = LOCAL / "datasets/gemini_v1"
terra = BenchmarkBuilder(LOCAL / "datasets/v2", queue).audit()
data = BenchmarkBuilder(gemini_root, queue).audit() if gemini_root.exists() else terra
engineering = BenchmarkBuilder(LOCAL / "datasets/v1", queue).audit()
batches = [read(path) for path in (LOCAL / "api/batches").glob("*/state.json")]
batch = max(batches, key=lambda item: item.get("started_at", "")) if batches else {}
api_calls = None
all_run_calls = None
ledger = LOCAL / "api/requests.sqlite3"
if ledger.exists() and batch:
    tasks = set(
        batch.get("completed", [])
        + batch.get("attempted_jobs", [])
        + batch.get("auxiliary_jobs", [])
    )
    discovery_task = "models:" + batch["batch_id"]
    tasks.add(discovery_task)
    started = datetime.fromisoformat(batch["started_at"]).timestamp()
    with sqlite3.connect(ledger) as connection:
        attempt_rows = list(connection.execute("SELECT task,started FROM attempts"))
        api_calls = sum(
            task in tasks and (task == discovery_task or timestamp >= started)
            for task, timestamp in attempt_rows
        )
        all_run_calls = sum(
            timestamp >= started or task == discovery_task for task, timestamp in attempt_rows
        )
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
        "batch_limit": policy["batch_size"],
        "daily_limit": policy["daily_limit"],
        "request_interval_seconds": policy["interval_seconds"],
        "status": batch.get("status", "not_started"),
        "reason": batch.get("reason"),
        "reported_batch_id": batch.get("batch_id"),
        "accepted_records_in_reported_batch": len(batch.get("completed", [])),
        "record_count_scope": "historical_successes_including_archived_revisions; active_valid_counts_are_in_dataset",
        "api_requests_in_reported_batch": api_calls,
        "project_api_requests_since_run_start_including_experiments": all_run_calls,
        "model_reviewed_sessions_in_run": batch.get("reviewed_sessions", 0),
        "continuation": "authorized_until_current_20_group_target_or_error_stop",
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
repair_file = gemini_root / "project/development_repair.json"
if repair_file.exists():
    repair = read(repair_file)
    report["development_data_repair"] = {
        "scope": "development_only",
        "first_invalidated_session": repair["first_session"],
        "invalidated_files": len(repair["invalidated"]),
        "archived_evidence_rows": repair["database_evidence_archived"],
        "original_sources_preserved": True,
        "new_content_requires_fresh_model_review": True,
    }
phase_file = LOCAL / "project/status.json"
graph_proof = gemini_root / "project/langgraph_smoke.json"
if graph_proof.exists():
    graph = read(graph_proof)
    jobs = [queue.get(value["job_id"]) for value in graph.get("jobs", {}).values()]
    if len(jobs) == 4 and all(
        j["status"] == "completed"
        and j["result_sha256"] == digest(j["result"])
        and j["provenance"].get("provider") == "openai-compatible"
        for j in jobs
    ):
        report["langgraph_api_verification"] = {
            "status": graph["status"],
            "actual_stage_calls": len(jobs),
            "memory_revisions": graph["memory_revisions"],
            "maximum_recorded_history_tokens": max(
                j["request"]["allowed_context"].get("history_tokens", 0) for j in jobs
            ),
            "requested_models": sorted({j["provenance"]["requested_model"] for j in jobs}),
            "returned_models": sorted({j["provenance"]["returned_model"] for j in jobs}),
            "scope": "development infrastructure proof, not benchmark effect or human endorsement",
        }
if phase_file.exists():
    phase = read(phase_file)
    report["project_workflow"] = {
        key: phase.get(key) for key in ("phase", "updated_at", "human_review")
    }
write(ROOT / "docs/status.json", report)
print(json.dumps(report, ensure_ascii=False, indent=2))
