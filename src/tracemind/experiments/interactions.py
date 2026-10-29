"""Two-round interactions gated on predefined targets and completed jobs."""

from __future__ import annotations

from tracemind.benchmark.builder import TEXT_SCHEMA, read, write

SCORE_SCHEMA = {
    "type": "object",
    "required": ["targets"],
    "properties": {
        "targets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "achieved", "evidence"],
                "properties": {
                    "id": {"type": "string"},
                    "achieved": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
            },
        }
    },
}


def advance_interaction(root, queue, checkpoint, method, candidate):
    target_path = root / "experiments" / "interaction_targets" / (checkpoint["id"] + ".json")
    if not target_path.exists():
        return {"status": "missing_targets"}
    spec = read(target_path)
    ids = spec.get("target_ids")
    private = spec.get("private_current")
    targets = spec.get("targets")
    if (
        not isinstance(ids, list)
        or not ids
        or any(not isinstance(x, str) or not x.strip() for x in ids)
        or len(set(ids)) != len(ids)
        or not isinstance(private, dict)
        or not isinstance(targets, list)
        or any(
            not isinstance(t, dict)
            or not isinstance(t.get("id"), str)
            or not isinstance(t.get("criterion"), str)
            or not t["criterion"].strip()
            for t in targets
        )
        or len(targets) != len(ids)
        or {t["id"] for t in targets} != set(ids)
    ):
        raise ValueError("interaction target IDs, definitions, and private_current are required")

    def get(purpose, role, context, schema):
        job_id = queue.enqueue(purpose, role, context, schema)
        job = queue.get(job_id)
        return job_id, job if job["status"] == "completed" else None

    cp_id = checkpoint["id"]
    transcript = [{"role": "counselor", "text": candidate["text"]}]
    provenance = []
    for round_number in (1, 2):
        client_context = {
            "private_current": private,
            "latest_client": checkpoint["latest_client"]["text"],
            "interaction": transcript,
            "instruction": "作为虚构来访者，只依据当前私有状态和可见互动自然回应；不要引入历史或未来设定。",
        }
        jid, job = get(
            f"interaction-client:{cp_id}:{method}:{round_number}",
            "simulated client",
            client_context,
            TEXT_SCHEMA,
        )
        if not job:
            return {"status": "waiting", "job_id": jid}
        transcript.append({"role": "client", "text": job["result"]["text"]})
        provenance.append({"job_id": jid, "result_sha256": job["result_sha256"]})
        if round_number == 1:
            counselor_context = {
                "history": candidate["context"]["history"],
                "current": {
                    "current_dialogue": checkpoint["current_dialogue"],
                    "latest_client": checkpoint["latest_client"],
                },
                "interaction": transcript,
                "instruction": "根据可见公开对话自然回应，不推测隐藏状态。",
            }
            jid, job = get(
                f"interaction-counselor:{cp_id}:{method}:2",
                "counselor",
                counselor_context,
                TEXT_SCHEMA,
            )
            if not job:
                return {"status": "waiting", "job_id": jid}
            transcript.append({"role": "counselor", "text": job["result"]["text"]})
            provenance.append({"job_id": jid, "result_sha256": job["result_sha256"]})
    jid, score = get(
        f"interaction-score:{cp_id}:{method}",
        "interaction scorer",
        {
            "targets": targets,
            "transcript": transcript,
            "instruction": "逐项评估预先指定目标，引用互动证据。",
        },
        SCORE_SCHEMA,
    )
    if not score:
        return {"status": "waiting", "job_id": jid}
    scores = score["result"]["targets"]
    if {item["id"] for item in scores} != set(ids) or len(scores) != len(ids):
        raise ValueError("interaction scorer must return each predefined target exactly once")
    if any(
        not item["evidence"].strip()
        or not any(item["evidence"] in turn["text"] for turn in transcript)
        for item in scores
    ):
        raise ValueError("interaction scoring evidence must quote a transcript turn")
    result = {
        "status": "completed",
        "target_ids": ids,
        "transcript": transcript,
        "jobs": provenance,
        "score_job_id": jid,
        "score_sha256": score["result_sha256"],
        "score": score["result"],
    }
    write(root / "experiments" / "interactions" / cp_id / (method + ".json"), result)
    return result
