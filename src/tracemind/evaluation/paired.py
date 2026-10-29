"""Analyze each paired contrast separately; never pool repeated evolution judgments."""

import random
from collections import defaultdict

from .metrics import ERROR_TYPES, _limited, summarize

SCORES = ("score_evidence_utilization", "score_understanding_update", "score_response_decision")


def paired_report(rows: list[dict], repeats: int = 2000, seed: int = 20261030):
    if repeats < 1:
        raise ValueError("bootstrap repeats must be positive")
    grouped = defaultdict(list)
    for row in rows:
        if not row.get("comparison_id") or not row.get("group_id"):
            raise ValueError("comparison_id and independent base group_id are required")
        grouped[row["comparison_id"]].append(row)
    result = {"judgment_design": "paired model quality; contrasts are not pooled", "contrasts": {}}
    for comparison, items in sorted(grouped.items()):
        methods = sorted({item["method"] for item in items})
        if len(methods) != 2:
            raise ValueError("each contrast needs both methods")
        left = "evolution" if "evolution" in methods else methods[0]
        right = next(method for method in methods if method != left)
        index = {}
        for item in items:
            key = (item["checkpoint_id"], item["method"])
            if key in index:
                raise ValueError("duplicate candidate judgment inside one contrast")
            index[key] = item
        report = {
            "left": left,
            "right": right,
            "by_method": {
                method: summarize([row for row in items if row["method"] == method])
                for method in methods
            },
            "differences": {},
        }
        checkpoints = sorted({item["checkpoint_id"] for item in items})
        for metric in (*ERROR_TYPES, *SCORES):
            by_group = defaultdict(list)
            for cp in checkpoints:
                a, b = index.get((cp, left)), index.get((cp, right))
                if a is None or b is None:
                    continue
                if a["group_id"] != b["group_id"]:
                    raise ValueError("paired checkpoint group mismatch")
                if metric in ERROR_TYPES and (_limited(a, metric) or _limited(b, metric)):
                    continue
                if metric not in a or metric not in b:
                    continue
                by_group[a["group_id"]].append(float(a[metric]) - float(b[metric]))
            values = [value for group in by_group.values() for value in group]
            if not values:
                report["differences"][metric] = {
                    "matched_checkpoints": 0,
                    "base_groups": 0,
                    "difference": None,
                    "ci95": None,
                }
                continue
            rng = random.Random(seed)
            groups = list(by_group)
            estimates = []
            for _ in range(repeats):
                sample = [
                    delta
                    for group in rng.choices(groups, k=len(groups))
                    for delta in by_group[group]
                ]
                estimates.append(sum(sample) / len(sample))
            estimates.sort()
            report["differences"][metric] = {
                "matched_checkpoints": len(values),
                "base_groups": len(groups),
                "difference": sum(values) / len(values),
                "ci95": [
                    estimates[int(0.025 * (repeats - 1))],
                    estimates[int(0.975 * (repeats - 1))],
                ],
            }
        result["contrasts"][comparison] = report
    return result
