"""Check saved API artifacts and rate-limit evidence without making requests."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime

from tracemind.api_client import read_policy
from tracemind.benchmark.builder import BenchmarkBuilder, read, write
from tracemind.config import LOCAL, dsn
from tracemind.jobs import JobQueue, digest


def audit(batch_id):
    policy, _ = read_policy()
    root = LOCAL / "api/batches" / batch_id
    state = read(root / "state.json")
    queue = JobQueue(dsn())
    issues = []
    jobs = queue.get_many(state["completed"])
    with sqlite3.connect(LOCAL / "api/requests.sqlite3") as connection:
        attempt_sources = {
            row[0]: row[1:]
            for row in connection.execute("SELECT id,input_sha,status FROM attempts")
        }
    model_pairs = set()
    anchors = utterances = 0
    for job_id in state["completed"]:
        artifact = read(root / (job_id + ".json"))
        job = jobs[job_id]
        if (
            job["status"] != "completed"
            or job["result_sha256"] != digest(artifact["result"])
            or job["provenance"] != artifact["provenance"]
            or artifact["actual_input_sha256"] != digest(artifact["actual_messages"])
        ):
            issues.append("artifact or source hash mismatch: " + job_id)
        provenance = artifact["provenance"]
        execution = provenance["execution_id"]
        # API batch v1 sends these fixed parameters; compare against the exact
        # serialized request-body hash recorded immediately before transport.
        payload = {
            "model": "gemini-3.8-flash",
            "messages": artifact["actual_messages"],
            "temperature": 0.7,
            "max_tokens": 600,
            "stream": False,
        }
        wire_sha = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
        if attempt_sources.get(execution) != (wire_sha, 200):
            issues.append("actual transport input mismatch: " + job_id)
        response_path = LOCAL / "api/responses" / (execution + ".json")
        if not response_path.exists():
            issues.append("missing saved transport response: " + job_id)
        else:
            response = read(response_path)
            choices = response.get("choices", [])
            content = choices[0].get("message", {}).get("content") if choices else None
            if content != artifact["result"]["text"] or response.get("model") != provenance.get(
                "returned_model"
            ):
                issues.append("saved response content or model mismatch: " + job_id)
        requested, returned = provenance.get("requested_model"), provenance.get("returned_model")
        model_pairs.add((requested, returned))
        if requested != "gemini-3.8-flash" or returned not in (
            requested,
            policy.get("approved_model_aliases", {}).get(requested),
        ):
            issues.append("unapproved model pair: " + job_id)
        actual = json.loads(artifact["actual_messages"][1]["content"])
        if job["request"]["purpose"].startswith("anchor:"):
            anchors += 1
        else:
            utterances += 1
            if job["request"]["role"] == "counselor" and {
                "profile",
                "experienced_events",
                "private_experiences",
            } & set(actual):
                issues.append("private role context leaked: " + job_id)
            if len(actual["current_dialogue"]) > 9:
                issues.append("unbounded current dialogue: " + job_id)
            if len(actual["historical_client_anchors"]) > 10:
                issues.append("unbounded historical context: " + job_id)
            for card in actual.get("experienced_events", []):
                if card["session"] > actual["session"]:
                    issues.append("future private card: " + job_id)
        if len(json.dumps(actual, ensure_ascii=False)) > 12000:
            issues.append("oversized role context: " + job_id)
    with sqlite3.connect(LOCAL / "api/requests.sqlite3") as connection:
        all_attempts = connection.execute(
            "SELECT task,started,day,status,outcome FROM attempts ORDER BY started"
        ).fetchall()
    started = datetime.fromisoformat(state["started_at"]).timestamp()
    tasks = set(
        state.get("attempted_jobs", []) + state["completed"] + state.get("auxiliary_jobs", [])
    )
    discovery = "models:" + batch_id
    attempts = [
        row
        for row in all_attempts
        if row[0] == discovery or (row[0] in tasks and row[1] >= started)
    ]
    intervals = [b[1] - a[1] for a, b in zip(all_attempts, all_attempts[1:])]
    if intervals and min(intervals) < policy["interval_seconds"]:
        issues.append("request start interval below policy")
    days = {row[2] for row in attempts}
    daily = {day: sum(row[2] == day for row in all_attempts) for day in sorted(days)}
    if policy["daily_limit"] is not None and any(
        value > policy["daily_limit"] for value in daily.values()
    ):
        issues.append("daily request budget exceeded")
    if (
        state["limit"] is not None
        and len(state["completed"]) > state["limit"]
        or policy["batch_size"] is not None
        and len(state["completed"]) > policy["batch_size"]
    ):
        issues.append("batch size exceeded")
    dataset = BenchmarkBuilder(LOCAL / "datasets" / state["dataset_namespace"], queue).audit()
    issues.extend(dataset["issues"])
    return {
        "batch_id": batch_id,
        "status": state["status"],
        "accepted_records": len(state["completed"]),
        "shared_openings": anchors,
        "dialogue_utterances": utterances,
        "http_attempts_in_batch": len(attempts),
        "http_status_counts": dict(
            Counter(str(row[3]) if row[3] is not None else "pending" for row in attempts)
        ),
        "minimum_project_request_start_interval_seconds": min(intervals) if intervals else None,
        "project_daily_attempts_including_other_tasks": daily,
        "model_pairs": [{"requested": a, "returned": b} for a, b in sorted(model_pairs)],
        "dataset": dataset,
        "issues": issues,
        "review_status": "automated structural checks; session model reviews tracked separately; no human review",
        "model_reviewed_sessions_reported_by_runner": state.get("reviewed_sessions", 0),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("batch_id")
    args = parser.parse_args()
    report = audit(args.batch_id)
    write(LOCAL / "api/audits" / (args.batch_id + ".json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(bool(report["issues"]))
