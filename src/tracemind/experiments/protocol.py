"""Calibration gate for a frozen, model-reviewed protocol."""

from __future__ import annotations

import json
from pathlib import Path

from tracemind.config import MODEL, REASONING
from tracemind.evaluation.protocol import QUALITY_SCHEMA, RUBRIC, VERSION, validate_quality
from tracemind.jobs import digest

TASK_A_RUBRIC = "仅依据公开对话评价理解的证据利用与更新，分别给0至4分，引用公开发言ID并解释。"
TASK_A_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["evidence_utilization", "understanding_update", "evidence_ids", "rationale"],
    "properties": {
        "evidence_utilization": {"type": "integer", "minimum": 0, "maximum": 4},
        "understanding_update": {"type": "integer", "minimum": 0, "maximum": 4},
        "evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "rationale": {"type": "string", "minLength": 1},
    },
}


def protocol_hash():
    return digest(
        {
            "version": VERSION,
            "rubric": RUBRIC,
            "quality_schema": QUALITY_SCHEMA,
            "task_a_rubric": TASK_A_RUBRIC,
            "task_a_schema": TASK_A_SCHEMA,
        }
    )


def test_review_ready(root: Path, queue) -> bool:
    path = root / "protocol" / "frozen.json"
    if not path.exists():
        return False
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (
        manifest.get("rubric_version") != VERSION
        or manifest.get("protocol_sha256") != protocol_hash()
    ):
        raise ValueError("live protocol differs from frozen manifest; use a new run directory")
    if (
        manifest.get("status") != "model_reviewed_not_human_gold"
        or len(manifest.get("examples", [])) < 12
        or len({e["group_id"] for e in manifest["examples"]}) < 3
    ):
        raise ValueError("invalid frozen calibration manifest")
    refs = [
        {
            "checkpoint_id": item["checkpoint_id"],
            "quality_job_id": item["quality_job_id"],
            "repeat_job_id": item["repeat_job_id"],
        }
        for item in manifest["examples"]
    ]
    freeze_protocol(root, queue, refs)
    return True


def freeze_protocol(root: Path, queue, refs: list[dict]) -> dict:
    if not isinstance(refs, list) or len(refs) < 12:
        raise ValueError("at least 12 calibration examples are required")
    examples, seen_checkpoints, seen_jobs, seen_actors = [], set(), set(), set()
    for ref in refs:
        cp_id, job_id, repeat_id = ref["checkpoint_id"], ref["quality_job_id"], ref["repeat_job_id"]
        if (
            not isinstance(cp_id, str)
            or Path(cp_id).name != cp_id
            or cp_id in seen_checkpoints
            or job_id == repeat_id
            or job_id in seen_jobs
            or repeat_id in seen_jobs
        ):
            raise ValueError("calibration checkpoints and review jobs must be unique")
        seen_checkpoints.add(cp_id)
        seen_jobs.update((job_id, repeat_id))
        cp = json.loads((root / "checkpoints" / (cp_id + ".json")).read_text(encoding="utf-8"))
        public_hash = cp.get("public_sha256")
        if cp.get("id") != cp_id or cp.get("split") != "calibration" or not cp.get("group_id"):
            raise ValueError("only calibration checkpoints with group IDs may be frozen")
        if public_hash != digest({k: v for k, v in cp.items() if k != "public_sha256"}):
            raise ValueError("calibration checkpoint hash mismatch")
        first, repeat = queue.get(job_id), queue.get(repeat_id)
        for job in (first, repeat):
            provenance = job.get("provenance") or {}
            if (
                job.get("status") != "completed"
                or not isinstance(job.get("result"), dict)
                or job.get("result_sha256") != digest(job["result"])
                or provenance.get("model") != MODEL
                or provenance.get("reasoning") != REASONING
                or not isinstance(provenance.get("agent_task"), str)
                or not provenance["agent_task"].strip()
            ):
                raise ValueError("calibration requires completed jobs with actual provenance")
            if job["request"].get("model") != MODEL or job["request"].get("reasoning") != REASONING:
                raise ValueError("calibration job model configuration differs")
            if digest(job["request"]) != job["id"]:
                raise ValueError("calibration job request hash mismatch")
        actors = {first["provenance"]["agent_task"], repeat["provenance"]["agent_task"]}
        if len(actors) != 2 or actors & seen_actors:
            raise ValueError("reviews must use independent agent tasks for every job")
        seen_actors.update(actors)
        a, b = first["request"], repeat["request"]
        context = a.get("allowed_context", {})
        if (
            not a.get("purpose", "").startswith("quality:")
            or b.get("purpose") != "calibration-repeat:" + job_id
            or a.get("role") != "blinded quality reviewer"
            or b.get("role") != "blinded quality reviewer"
            or a.get("output_schema") != QUALITY_SCHEMA
            or b.get("output_schema") != QUALITY_SCHEMA
            or context != b.get("allowed_context")
        ):
            raise ValueError("repeat must independently review the same anonymous input")
        if (
            set(context)
            != {
                "public_history",
                "current",
                "response_a",
                "response_b",
                "instruction",
                "rubric_version",
            }
            or context["public_history"] != cp["previous_sessions"]
            or context["current"]
            != {"current_dialogue": cp["current_dialogue"], "latest_client": cp["latest_client"]}
            or context["instruction"] != RUBRIC
            or context["rubric_version"] != VERSION
        ):
            raise ValueError("review input must match the public checkpoint and frozen rubric")
        allowed = {u["id"] for session in cp["previous_sessions"] for u in session["utterances"]}
        allowed.update(u["id"] for u in cp["current_dialogue"])
        allowed.add(cp["latest_client"]["id"])
        for job in (first, repeat):
            validate_quality(
                job["result"], {"a": context["response_a"], "b": context["response_b"]}, allowed
            )
        examples.append(
            {
                "checkpoint_id": cp_id,
                "group_id": cp["group_id"],
                "public_sha256": public_hash,
                "quality_job_id": job_id,
                "quality_result_sha256": first["result_sha256"],
                "repeat_job_id": repeat_id,
                "repeat_result_sha256": repeat["result_sha256"],
            }
        )
    if len({item["group_id"] for item in examples}) < 3:
        raise ValueError("at least three calibration groups are required")
    manifest = {
        "status": "model_reviewed_not_human_gold",
        "rubric_version": VERSION,
        "protocol_sha256": protocol_hash(),
        "examples": sorted(examples, key=lambda x: x["checkpoint_id"]),
    }
    path = root / "protocol" / "frozen.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except FileExistsError:
        if json.loads(path.read_text(encoding="utf-8")) != manifest:
            raise ValueError("protocol already frozen; use a new run directory")
    return manifest
