"""Paired exploratory comparisons and resource accounting for mechanism arms."""
import csv
import json
import statistics
from pathlib import Path

import p1
import analyze
import mechanisms

ROOT = p1.ROOT
OUT = ROOT / "analysis"
METRICS_A = analyze.METRICS_A
METRICS_B = analyze.METRICS_B
ERRORS = analyze.ERRORS


def usage_of(records):
    out = {"calls": len(records), "input_tokens": 0, "output_tokens": 0, "latency_seconds": 0.0}
    for r in records:
        u = r.get("usage") or {}
        out["input_tokens"] += u.get("input_tokens", u.get("prompt_tokens", 0)) or 0
        out["output_tokens"] += u.get("output_tokens", u.get("completion_tokens", 0)) or 0
        out["latency_seconds"] += r.get("latency_seconds", 0) or 0
    out["latency_seconds"] = round(out["latency_seconds"], 2)
    return out


def records(pattern):
    return [p1.read(p) for p in ROOT.glob(pattern)]


def mechanism_rows():
    mapping = p1.read(ROOT / "mechanisms/internal_blind_key.json")
    rows = []
    for name in p1.NODES:
        for label, arm in mapping[name].items():
            condition, task = arm
            a = p1.read(ROOT / "mechanisms/reviews" / name / (label + "_" + task + ".json"))["assessment"]
            row = {"case_id": name[:3], "checkpoint": name, "condition": condition, "task": task,
                   "candidate_id": label}
            for metric in METRICS_A if task == "A" else METRICS_B:
                row[metric] = a["scores"][metric]
            if task == "B":
                for err in ERRORS:
                    row[err] = bool(a["errors"][err]["occurred"])
                    row[err + "_access_limited"] = bool(a["errors"][err].get("access_limited", False))
            rows.append(row)
    return rows


def budget_rows():
    mapping = p1.read(ROOT / "budget_match/internal_blind_key.json")
    rows = []
    for name in p1.NODES:
        for label, condition in mapping[name].items():
            a = p1.read(ROOT / "budget_match/reviews" / name / (label + ".json"))["assessment"]
            row = {"case_id": name[:3], "checkpoint": name, "condition": condition,
                   "task": "B", "candidate_id": label}
            for metric in METRICS_B:
                row[metric] = a["scores"][metric]
            for err in ERRORS:
                row[err] = bool(a["errors"][err]["occurred"])
                row[err + "_access_limited"] = bool(a["errors"][err].get("access_limited", False))
            rows.append(row)
    return rows


def full_access_rows():
    mapping = p1.read(ROOT / "full_access/internal_blind_key.json")
    rows = []
    for name in p1.NODES:
        for label, (condition, task) in mapping[name].items():
            a = p1.read(ROOT / "full_access/reviews" / name / (label + ".json"))["assessment"]
            row = {"case_id": name[:3], "checkpoint": name, "condition": condition,
                   "task": task, "candidate_id": label}
            for metric in METRICS_A if task == "A" else METRICS_B:
                row[metric] = a["scores"][metric]
            if task == "B":
                for err in ERRORS:
                    row[err] = bool(a["errors"][err]["occurred"])
                    row[err + "_access_limited"] = bool(a["errors"][err].get("access_limited", False))
            rows.append(row)
    return rows


def compare(rows, task, conditions, metrics):
    subset = [r for r in rows if r["task"] == task and r["condition"] in conditions]
    shared = set.intersection(*(set(r["checkpoint"] for r in subset if r["condition"] == condition)
                                for condition in conditions))
    subset = [r for r in subset if r["checkpoint"] in shared]
    if {r["condition"] for r in subset} != set(conditions):
        raise RuntimeError("Missing comparison arm " + str(conditions))
    result = {"conditions": list(conditions), "metrics": {}, "n_cases": len({r["case_id"] for r in subset}),
              "n_checkpoints_per_condition": len(subset) // len(conditions)}
    for metric in metrics:
        values = {}
        for condition in conditions:
            values[condition] = analyze.mean([r[metric] for r in subset if r["condition"] == condition])
        a, b = conditions
        fn = lambda sample: analyze.mean([r[metric] for r in sample if r["condition"] == a]) - analyze.mean(
                            [r[metric] for r in sample if r["condition"] == b])
        result["metrics"][metric] = {"means": values, "difference_" + a + "_minus_" + b: fn(subset),
                                     "cluster_bootstrap_ci95": list(analyze.bootstrap(subset, fn))}
    return result


def run():
    rows = mechanism_rows()
    budgets = budget_rows()
    full_rows = full_access_rows()
    if len(rows) != 126 or len(budgets) != 48 or len(full_rows) != 96:
        raise RuntimeError("Incomplete mechanism/budget reviews")
    all_rows = rows + budgets + full_rows
    fields = sorted({k for r in all_rows for k in r})
    with (OUT / "mechanism_scores.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)
    result = {"primary_A_C5_vs_C4": compare(rows, "A", ("C5", "C4"), METRICS_A),
              "primary_B_C5_vs_C4": compare(rows, "B", ("C5", "C4"), METRICS_B),
              "ablation_B_C5_vs_C5_minus": compare(rows, "B", ("C5", "C5_minus"), METRICS_B),
              "budget_B_C5_vs_C4": compare(budgets, "B", ("C5_budget", "C4_budget"), METRICS_B),
              "full_access_A_C5_vs_C4_full": compare(full_rows, "A", ("C5", "C4_full"), METRICS_A),
              "full_access_B_C5_vs_C4_full": compare(full_rows, "B", ("C5", "C4_full"), METRICS_B),
              "direct_probe_B_C5_vs_direct": compare(rows, "B", ("C5", "direct"), METRICS_B),
              "direct_probe_B_C4_vs_direct": compare(rows, "B", ("C4", "direct"), METRICS_B),
              "errors": {}, "resources": {},
              "caveats": ["All scores are from a model judge, not clinicians.",
                          "Direct-response probe has six preselected checkpoints, not 24.",
                          "C4 reconstructs from temporal retrieval; C5 incrementally sees each full prior session.",
                          "Both response arms receive the same full public history and current dialogue.",
                          "The budget sensitivity equalizes a 1,200-character formulation cap, not cumulative inference compute."]}
    for condition in ("C4", "C5", "C5_minus", "direct", "C4_budget", "C5_budget", "C4_full"):
        source = full_rows if condition == "C4_full" else (budgets if condition.endswith("_budget") else rows)
        group = [r for r in source if r["condition"] == condition and r["task"] == "B"]
        result["errors"][condition] = {err: {"count": sum(r[err] for r in group), "n": len(group),
                                               "rate": analyze.mean([int(r[err]) for r in group])}
                                       for err in ERRORS}
    c4_form = records("mechanisms/C4_formulations/*.json")
    c5_snap = records("mechanisms/C5_snapshots/*.json")
    c4_resp = records("mechanisms/answers/*/C4_B.json")
    c5_resp = records("mechanisms/answers/*/C5_B.json")
    minus_resp = records("mechanisms/answers/*/C5_minus_B.json")
    direct_resp = records("mechanisms/answers/*/direct_B.json")
    budget_resp = records("budget_match/answers/*.json")
    full_form = records("full_access/formulations/*.json")
    full_resp = records("full_access/answers/*.json")
    result["resources"] = {
        "C4_formulation": usage_of(c4_form), "C5_updates": usage_of(c5_snap),
        "C4_responses": usage_of(c4_resp), "C5_responses": usage_of(c5_resp),
        "C5_minus_responses": usage_of(minus_resp), "direct_probe_responses": usage_of(direct_resp),
        "C5_budget_responses": usage_of(budget_resp),
        "C4_full_formulation": usage_of(full_form), "C4_full_responses": usage_of(full_resp),
        "formulation_characters": {
            "C4_mean": analyze.mean([x["memory_characters"] for x in c4_form]),
            "C5_snapshot_mean": analyze.mean([x["memory_characters"] for x in c5_snap]),
            "C5_budget_mean": analyze.mean([x["characters"] for x in records("budget_match/C5_memories/*.json")]),
            "C5_budget_cap": 1200},
        "budget_trim_removed": {field: sum(x["removed_or_shortened"][field] for x in
                                    records("budget_match/C5_memories/*.json")) for field in p1.MEMORY_FIELDS}}
    result["resources"]["C4_total_generation"] = {
        key: result["resources"]["C4_formulation"][key] + result["resources"]["C4_responses"][key]
        for key in ("calls", "input_tokens", "output_tokens", "latency_seconds")}
    result["resources"]["C5_total_generation"] = {
        key: result["resources"]["C5_updates"][key] + result["resources"]["C5_responses"][key]
        for key in ("calls", "input_tokens", "output_tokens", "latency_seconds")}
    result["resources"]["C4_full_total_generation"] = {
        key: result["resources"]["C4_full_formulation"][key] + result["resources"]["C4_full_responses"][key]
        for key in ("calls", "input_tokens", "output_tokens", "latency_seconds")}
    p1.write(OUT / "mechanism_summary.json", result)
    print("Mechanism analysis complete", flush=True)


if __name__ == "__main__":
    run()
