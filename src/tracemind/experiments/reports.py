"""Measured summaries only, with explicit denominators and missing telemetry."""

import random
import sqlite3
from collections import defaultdict

from tracemind.benchmark.builder import read
from tracemind.config import LOCAL
from tracemind.evaluation.metrics import ERROR_TYPES, agreement
from tracemind.jobs import digest

from .runner import METHODS


def method_cost_report(root, queue, checkpoints):
    cases = {cp["case_id"] for cp in checkpoints}
    result = {}
    with sqlite3.connect(LOCAL / "api/requests.sqlite3") as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(attempts)")}
        durations = {
            row[0]: max(0, row[2] - row[1]) if row[2] is not None else None
            for row in db.execute(
                "SELECT id,started,"
                + ("finished" if "finished" in columns else "NULL")
                + " FROM attempts"
            )
        }
    for method in METHODS:
        answer_ids = []
        history_tokens = []
        for cp in checkpoints:
            for directory in ("candidates", "understanding"):
                item = read(root / "experiments" / directory / cp["id"] / (method + ".json"))
                answer_ids.append(item["job_id"])
                if directory == "candidates":
                    history_tokens.append(item["context"]["history_tokens"])
        update_ids = set()
        for case in cases:
            for path in (root / "experiments/snapshots" / method / case).glob("*.json"):
                update_ids.add(read(path)["job_id"])
        groups = {"answer_tasks": answer_ids, "unique_preparation_update_tasks": sorted(update_ids)}
        output = {
            "history_budget": 4096,
            "maximum_answer_history_tokens": max(history_tokens, default=0),
        }
        for label, identifiers in groups.items():
            usage = defaultdict(int)
            measured = []
            missing = 0
            for ident in identifiers:
                job = queue.get(ident)
                if job["status"] != "completed":
                    raise ValueError("unfinished job in method cost report")
                provenance = job["provenance"]
                for key, value in (provenance.get("usage") or {}).items():
                    if isinstance(value, int):
                        usage[key] += value
                duration = durations.get(provenance.get("execution_id"))
                if duration is None:
                    missing += 1
                else:
                    measured.append(duration)
            output[label] = {
                "calls": len(identifiers),
                "provider_usage_totals": dict(usage),
                "duration_seconds_sum": sum(measured),
                "duration_measurements": len(measured),
                "duration_missing": missing,
            }
        result[method] = output
    return {
        "scope": "test answer tasks plus deduplicated test-case preparation/update jobs; excludes judges and dialogue synthesis",
        "methods": result,
    }


def checked(queue, job_id, sha, result):
    job = queue.get(job_id)
    if job["status"] != "completed" or job["result_sha256"] != sha or digest(result) != sha:
        raise ValueError("report source result/hash mismatch")
    return job


def task_a_report(root, queue, checkpoints):
    values = defaultdict(list)
    for cp in checkpoints:
        for method in METHODS:
            item = read(root / "experiments/understanding_quality" / cp["id"] / (method + ".json"))
            checked(queue, item["job_id"], item["result_sha256"], item["result"])
            values[method].append(item["result"])
    return {
        method: {
            "checkpoints": len(rows),
            **{
                dimension + "_mean": sum(row[dimension] for row in rows) / len(rows)
                for dimension in ("evidence_utilization", "understanding_update")
            },
        }
        for method, rows in values.items()
    }


def repeat_report(root, queue):
    manifest = read(root / "protocol/frozen.json")
    scores = defaultdict(list)
    errors = {kind: [] for kind in ERROR_TYPES}
    for example in manifest["examples"]:
        first, repeat = (queue.get(example[key]) for key in ("quality_job_id", "repeat_job_id"))
        checked(queue, first["id"], example["quality_result_sha256"], first["result"])
        checked(queue, repeat["id"], example["repeat_result_sha256"], repeat["result"])
        for side in ("a", "b"):
            a, b = first["result"][side], repeat["result"][side]
            for dimension in a["scores"]:
                scores[dimension].append(abs(a["scores"][dimension] - b["scores"][dimension]))
            for kind in ERROR_TYPES:
                errors[kind].append(
                    ({kind: a["errors"][kind]["occurred"]}, {kind: b["errors"][kind]["occurred"]})
                )
    return {
        "calibration_checkpoints": len(manifest["examples"]),
        "mean_absolute_score_difference": {
            key: sum(rows) / len(rows) for key, rows in scores.items()
        },
        "error_label_agreement": {key: agreement(rows, key) for key, rows in errors.items()},
        "rater_type": "independent requests of the same model; not human inter-rater reliability",
    }


def clarification_report(root, queue, checkpoints):
    result = {}
    observed = {}
    for method in ("temporalrag", "on_demand", "evolution"):
        observed[method] = {}
        n = eligible = success = target_n = target_hits = flagged = 0
        for cp in checkpoints:
            item = read(root / "experiments/interactions" / cp["id"] / (method + ".json"))
            checked(queue, item["score_job_id"], item["score_sha256"], item["score"])
            check = read(root / "experiments/simulator_checks" / cp["id"] / (method + ".json"))
            checked(queue, check["job_id"], check["result_sha256"], check["result"])
            n += 1
            if check["result"]["unsupported_agreement"]:
                flagged += 1
                continue
            eligible += 1
            targets = item["score"]["targets"]
            target_n += len(targets)
            target_hits += sum(t["achieved"] for t in targets)
            success += any(t["achieved"] for t in targets)
            observed[method][cp["id"]] = (
                cp["group_id"],
                float(any(t["achieved"] for t in targets)),
            )
        result[method] = {
            "interactions": n,
            "simulator_inconsistency_flagged": flagged,
            "eligible_interactions": eligible,
            "interactions_clarifying_any_predefined_target": success,
            "interaction_success_rate": success / eligible if eligible else None,
            "predefined_targets": target_n,
            "accurately_clarified_targets": target_hits,
            "target_clarification_rate": target_hits / target_n if target_n else None,
        }
    return {
        "definition": "at least one predefined target achieved in two rounds, excluding simulator-inconsistency flags; synthetic outcomes only",
        "methods": result,
        "paired_differences_vs_temporalrag": {
            method: clustered_difference(observed[method], observed["temporalrag"])
            for method in ("on_demand", "evolution")
        },
    }


def clustered_difference(left, right, repeats=2000):
    groups = defaultdict(list)
    for key in left.keys() & right.keys():
        group, a = left[key]
        other, b = right[key]
        if group != other:
            raise ValueError("clarification pair base-group mismatch")
        groups[group].append(a - b)
    values = [value for rows in groups.values() for value in rows]
    if not values:
        return {"matched_checkpoints": 0, "base_groups": 0, "difference": None, "ci95": None}
    rng = random.Random(20261030)
    estimates = []
    for _ in range(repeats):
        sample = [
            value for group in rng.choices(list(groups), k=len(groups)) for value in groups[group]
        ]
        estimates.append(sum(sample) / len(sample))
    estimates.sort()
    return {
        "matched_checkpoints": len(values),
        "base_groups": len(groups),
        "difference": sum(values) / len(values),
        "ci95": [estimates[int(0.025 * (repeats - 1))], estimates[int(0.975 * (repeats - 1))]],
    }


def telemetry_report(root):
    with sqlite3.connect(LOCAL / "api/requests.sqlite3") as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(attempts)")}
        rows = db.execute(
            "SELECT started,status,outcome"
            + (",finished" if "finished" in columns else ",NULL")
            + " FROM attempts"
        ).fetchall()
    durations = [max(0, row[3] - row[0]) for row in rows if row[3] is not None]
    usage = defaultdict(int)
    responses_with_usage = 0
    for path in (LOCAL / "api/responses").glob("*.json"):
        values = read(path).get("usage")
        if isinstance(values, dict):
            responses_with_usage += 1
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if isinstance(values.get(key), int):
                    usage[key] += values[key]
    traces = [read(path) for path in (root / "experiments/retrieval").glob("*.json")]
    times = [
        item["retrieval_elapsed_seconds"] for item in traces if "retrieval_elapsed_seconds" in item
    ]
    return {
        "scope": "all project API attempts including initial diagnostics/rejections; not provider-account-wide usage",
        "http_attempts": len(rows),
        "http_200": sum(row[1] == 200 for row in rows),
        "attempts_with_duration": len(durations),
        "attempts_missing_duration": len(rows) - len(durations),
        "mean_http_attempt_duration_seconds": sum(durations) / len(durations)
        if durations
        else None,
        "provider_reported_usage_totals": dict(usage),
        "responses_with_usage": responses_with_usage,
        "fee": None,
        "fee_reason": "provider charges/prices unavailable; no inferred cost",
        "retrievals_with_timing": len(times),
        "retrievals_missing_timing": len(traces) - len(times),
        "mean_initial_retrieval_seconds": sum(times) / len(times) if times else None,
    }
