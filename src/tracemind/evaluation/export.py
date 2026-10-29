"""Build real human review packets only from completed held-out candidates."""

from pathlib import Path

from tracemind.benchmark.builder import read, write
from tracemind.jobs import digest

from .packets import make_packets


def export_packets(root: Path, destination: Path, queue, seed: int = 20261030):
    checkpoints = [
        read(p)
        for p in sorted((root / "checkpoints").glob("*.json"))
        if read(p).get("split") == "test"
    ]
    if len(checkpoints) != 60:
        raise ValueError(
            f"review packets pending: {len(checkpoints)}/60 held-out checkpoints exist"
        )
    items = []
    for cp in checkpoints:
        values = {}
        for method in ("evolution", "temporalrag", "on_demand", "summary", "current"):
            path = root / "experiments/candidates" / cp["id"] / (method + ".json")
            if not path.exists():
                raise ValueError("review packets pending: candidate missing")
            candidate = read(path)
            job = queue.get(candidate["job_id"])
            if (
                job["status"] != "completed"
                or job["result_sha256"] != candidate["result_sha256"]
                or digest({"text": candidate["text"]}) != job["result_sha256"]
            ):
                raise ValueError("candidate result provenance mismatch")
            values[method] = candidate["text"]
        items.append(
            {
                "group_id": cp["group_id"],
                "case_id": cp["case_id"],
                "checkpoint_id": cp["id"],
                "public_history": {
                    "previous_sessions": cp["previous_sessions"],
                    "current_dialogue": cp["current_dialogue"],
                    "latest_client": cp["latest_client"],
                },
                "candidates": values,
            }
        )
    groups = sorted({i["group_id"] for i in items})
    current = []
    for group in groups:
        group_items = sorted(
            (i for i in items if i["group_id"] == group), key=lambda i: i["checkpoint_id"]
        )
        # Four trajectories, distributing the selected stages across the group.
        current.extend(group_items[index] for index in (0, 4, 8, 9))
    packets = make_packets(items, current, seed)
    write(destination, packets)
    return {
        "paired_packets": len(packets),
        "human_judgments_required": 2 * len(packets),
        "human_judgments_completed": 0,
    }
