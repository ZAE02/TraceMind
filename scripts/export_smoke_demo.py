"""Publish a complete development-only input/answer demonstration, never test data."""

import json

from tracemind.benchmark.builder import read, write
from tracemind.config import LOCAL, ROOT, dsn
from tracemind.experiments.runner import METHODS
from tracemind.jobs import JobQueue, digest

root = LOCAL / "datasets/gemini_v1"
cp = read(root / "checkpoints/gemini_v1_G001-T1-CP1.json")
if cp["split"] != "development":
    raise ValueError("only development checkpoints can be published")
queue = JobQueue(dsn())
history_ids = {u["id"] for session in cp["previous_sessions"] for u in session["utterances"]}
methods = {}
for method in METHODS:
    candidate = read(root / "experiments/candidates" / cp["id"] / (method + ".json"))
    job = queue.get(candidate["job_id"])
    if (
        job["status"] != "completed"
        or job["result_sha256"] != candidate["result_sha256"]
        or digest({"text": candidate["text"]}) != job["result_sha256"]
    ):
        raise ValueError("candidate source mismatch")
    raw = read(root / "experiments/api_raw" / (candidate["job_id"] + ".json"))
    actual = json.loads(raw["actual_messages"][1]["content"])["task"]
    if set(actual) != {"history", "current", "instruction"}:
        raise ValueError("unexpected answer context fields")
    if digest(actual["history"]) != digest(candidate["context"]["history"]):
        raise ValueError("actual answer input mismatch")
    if digest(actual["current"]) != digest(
        {"current_dialogue": cp["current_dialogue"], "latest_client": cp["latest_client"]}
    ):
        raise ValueError("actual current context mismatch")
    exposed = {row["id"] for row in actual["history"]["evidence"]}
    memory = actual["history"]["memory"]
    if isinstance(memory, dict):
        exposed.update(row["id"] for row in memory.get("evidence", []))
    if not exposed <= history_ids:
        raise ValueError("answer history includes evidence outside frozen public history")
    if candidate["context"]["history_tokens"] > 4096:
        raise ValueError("history budget exceeded")
    methods[method] = {
        "answer": candidate["text"],
        "actual_task_input": actual,
        "actual_input_sha256": raw["actual_input_sha256"],
        "requested_model": raw["provenance"]["requested_model"],
        "returned_model": raw["provenance"]["returned_model"],
        "history_tokens": candidate["context"]["history_tokens"],
        "source_job_id": candidate["job_id"],
        "result_sha256": candidate["result_sha256"],
    }
write(
    ROOT / "data/examples/gemini_v1/checkpoint_demo.json",
    {
        "checkpoint_id": cp["id"],
        "split": "development",
        "synthetic": True,
        "history_budget": 4096,
        "methods": methods,
        "status": "actual six-method development smoke; not a test-set effect or human/clinical validation",
        "hidden_private_cards_included": False,
        "preparation_policy_note": "legacy on-demand development snapshot retained; formal calibration/test use hybrid-evidence-rebuild-v2",
    },
)
print("Published six-method development demonstration from verified actual inputs and responses")
