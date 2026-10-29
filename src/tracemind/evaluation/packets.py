"""Blinded paired evaluation packet construction."""

from __future__ import annotations

import random
from collections import Counter
from typing import Any

COMPARISONS = ("temporalrag", "on_demand", "summary")


def make_packets(
    checkpoints: list[dict[str, Any]], current: list[dict[str, Any]], seed: int = 0
) -> list[dict[str, Any]]:
    """Create 180 longitudinal comparisons and 20 current-only comparisons.

    Each checkpoint supplies case_id, checkpoint_id, public_history and candidates,
    keyed by evolution and the three comparators. Current entries supply the same
    identifiers and public_history, with candidates keyed evolution and current.
    Candidate values must be strings. Input and provenance are intentionally not
    projected into the public packet.
    """
    if len(checkpoints) != 60 or len(current) != 20:
        raise ValueError("expected 60 checkpoints and 20 current comparisons")
    ids = [x["checkpoint_id"] for x in checkpoints]
    current_ids = [x["checkpoint_id"] for x in current]
    groups = Counter(x["group_id"] for x in checkpoints)
    if len(set(ids)) != 60 or len(set(current_ids)) != 20 or not set(current_ids) <= set(ids):
        raise ValueError(
            "checkpoint IDs must be unique; current comparisons must use the same test set"
        )
    if len(groups) != 5 or set(groups.values()) != {12}:
        raise ValueError(
            "test packets require five independent base scenes, twelve checkpoints each"
        )
    if Counter(x["group_id"] for x in current) != Counter({g: 4 for g in groups}):
        raise ValueError("current comparisons require four checkpoints per base scene")
    original = {item["checkpoint_id"]: item for item in checkpoints}
    for item in current:
        source = original[item["checkpoint_id"]]
        if (
            any(item[key] != source[key] for key in ("case_id", "group_id", "public_history"))
            or item["candidates"]["evolution"] != source["candidates"]["evolution"]
        ):
            raise ValueError("current comparison differs from its frozen checkpoint")
    rng = random.Random(seed)
    packets = []
    for item, opponents in [(x, COMPARISONS) for x in checkpoints] + [
        (x, ("current",)) for x in current
    ]:
        for opponent in opponents:
            left, right = "evolution", opponent
            if rng.randrange(2):
                left, right = right, left
            candidates = item["candidates"]
            if not all(
                isinstance(candidates[m], str) and candidates[m].strip() for m in (left, right)
            ):
                raise ValueError("candidate responses must be nonempty strings")
            packets.append(
                {
                    "id": str(len(packets) + 1),
                    "group_id": str(item["group_id"]),
                    "case_id": str(item["case_id"]),
                    "checkpoint_id": str(item["checkpoint_id"]),
                    "public_history": item["public_history"],
                    "response_a": candidates[left],
                    "response_b": candidates[right],
                    "mapping": {"a": left, "b": right},
                }
            )
    return packets


def public_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Project only quality-review material, never candidate input or method."""
    return {
        key: packet[key]
        for key in ("id", "case_id", "checkpoint_id", "public_history", "response_a", "response_b")
    }
