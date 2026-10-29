"""Review metrics, keeping inaccessible evidence out of error denominators."""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any

ERROR_TYPES = ("unsupported_assertion", "leading_question", "missed_progress")


def _limited(row, field=None):
    access = row.get("access_limited", False)
    if isinstance(access, dict):
        return (
            bool(access.get(field, False))
            if field
            else all(access.get(k, False) for k in ERROR_TYPES)
        )
    return bool(access)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [r for r in rows if not _limited(r)]
    counts = {
        key: sum(bool(r.get(key)) for r in rows if not _limited(r, key)) for key in ERROR_TYPES
    }
    denominators = {key: sum(not _limited(r, key) for r in rows) for key in ERROR_TYPES}
    return {
        "total": len(rows),
        "access_limited": len(rows) - len(eligible),
        "denominator": len(eligible),
        "counts": counts,
        "denominators": denominators,
        "rates": {
            key: counts[key] / denominators[key] if denominators[key] else None
            for key in ERROR_TYPES
        },
        "tentative_exploration_count": sum(bool(r.get("tentative_exploration")) for r in eligible),
    }


def cluster_bootstrap(
    rows: list[dict[str, Any]], field: str, iterations: int = 2000, seed: int = 0
) -> tuple[float, float] | None:
    if field not in ERROR_TYPES or iterations < 1:
        raise ValueError("invalid field or iterations")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not _limited(row, field):
            if not row.get("group_id"):
                raise ValueError(
                    "group_id is required: trajectories from a base scene are not independent"
                )
            groups[str(row["group_id"])].append(row)
    if not groups:
        return None
    rng = random.Random(seed)
    cases = list(groups)
    estimates = []
    for _ in range(iterations):
        sample = [r for _ in cases for r in groups[rng.choice(cases)]]
        estimates.append(sum(bool(r.get(field)) for r in sample) / len(sample))
    estimates.sort()
    return estimates[int(0.025 * (iterations - 1))], estimates[int(0.975 * (iterations - 1))]


def agreement(pairs: list[tuple[dict[str, Any], dict[str, Any]]], field: str) -> dict[str, Any]:
    eligible = [
        (a, b)
        for a, b in pairs
        if not _limited(a, field) and not _limited(b, field) and field in a and field in b
    ]
    n = len(eligible)
    if not n:
        return {"denominator": 0, "observed": None, "kappa": None}
    observed = sum(a[field] == b[field] for a, b in eligible) / n
    pa = sum(bool(a[field]) for a, _ in eligible) / n
    pb = sum(bool(b[field]) for _, b in eligible) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    return {
        "denominator": n,
        "observed": observed,
        "kappa": (observed - expected) / (1 - expected) if expected != 1 else None,
    }


def review_rows(
    group_id: str, checkpoint_id: str, quality: dict, accesses: dict[str, dict]
) -> list[dict[str, Any]]:
    """Map paired quality and both access reviews to per-error metric rows."""
    if not group_id or set(accesses) != {"a", "b"}:
        raise ValueError("group_id and access reviews for both sides are required")
    rows = []
    for side in ("a", "b"):
        result = quality["result"][side]
        access = accesses[side]["result"]["access_limited_by_error"]
        if set(access) != set(ERROR_TYPES):
            raise ValueError("access flags must cover each error")
        rows.append(
            {
                "group_id": group_id,
                "checkpoint_id": checkpoint_id,
                "comparison": quality["mapping"],
                "method": quality["mapping"][side],
                **{key: result["errors"][key]["occurred"] for key in ERROR_TYPES},
                "access_limited": dict(access),
                "tentative_exploration": result["tentative_exploration"],
            }
        )
    return rows


def collect_metric_rows(root) -> list[dict[str, Any]]:
    """Only include pairs with a quality review and both access reviews."""
    import json
    from pathlib import Path

    root = Path(root)
    rows = []
    for path in sorted((root / "experiments" / "quality").glob("*/*.json")):
        cp_id, comparison = path.parent.name, path.stem
        cp_path = root / "checkpoints" / (cp_id + ".json")
        if not cp_path.exists():
            raise ValueError(f"missing checkpoint: {cp_id}")
        quality = json.loads(path.read_text(encoding="utf-8"))
        accesses = {}
        for side in ("a", "b"):
            method = quality["mapping"][side]
            access_path = root / "experiments" / "access" / cp_id / f"{comparison}--{method}.json"
            if not access_path.exists():
                break
            access = json.loads(access_path.read_text(encoding="utf-8"))
            if access["quality_job_id"] != quality["job_id"] or access["side"] != side:
                raise ValueError("access review does not match its quality finding")
            accesses[side] = access
        if len(accesses) == 2:
            cp = json.loads(cp_path.read_text(encoding="utf-8"))
            rows.extend(review_rows(cp["group_id"], cp_id, quality, accesses))
    return rows
