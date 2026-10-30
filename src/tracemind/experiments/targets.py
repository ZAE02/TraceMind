"""Freeze clarification targets from current cards before answer jobs exist."""

from tracemind.api_client import APIStopped
from tracemind.benchmark.builder import BRANCHES, read, write
from tracemind.jobs import digest

from .api_executor import execute_job

SCHEMA = {
    "type": "object",
    "required": ["targets"],
    "additionalProperties": False,
    "properties": {
        "targets": {
            "type": "array",
            "minItems": 1,
            "maxItems": 2,
            "items": {
                "type": "object",
                "required": ["criterion", "source_field", "source_index"],
                "properties": {
                    "criterion": {"type": "string", "minLength": 1},
                    "source_field": {"enum": ["private_experiences", "occurred_events"]},
                    "source_index": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
        }
    },
}


def current_card(root, checkpoint):
    plan = read(root / "plans" / (checkpoint["group_id"] + ".json"))
    branch = BRANCHES[int(checkpoint["case_id"].rsplit("-T", 1)[1]) - 1]
    return plan["profile"], plan["branches"][branch]["sessions"][checkpoint["session"] - 1]


def prepare_targets(root, queue, api, checkpoint):
    path = root / "experiments/interaction_targets" / (checkpoint["id"] + ".json")
    profile, card = current_card(root, checkpoint)
    source_sha = digest({"public": checkpoint["public_sha256"], "profile": profile, "card": card})
    if path.exists():
        saved = read(path)
        if saved.get("source_sha256") != source_sha:
            raise APIStopped("frozen clarification target source changed")
        if (
            checkpoint.get("split") in ("calibration", "test")
            and saved.get("protocol") != "target-v2"
        ):
            raise APIStopped(
                "formal clarification targets require full-visible-history review protocol v2"
            )
        return saved
    if list((root / "experiments/candidates" / checkpoint["id"]).glob("*.json")):
        raise APIStopped("cannot define clarification targets after candidate answers")
    context = {
        "current_public": checkpoint["latest_client"],
        "previous_public_sessions": [
            {
                "session": s["session"],
                "utterances": [
                    {key: u[key] for key in ("id", "role", "text")} for u in s["utterances"]
                ],
            }
            for s in checkpoint.get("previous_sessions", [])
        ],
        "current_card": card,
        "instruction": "在答卷生成前预先指定一至两个有意义、尚未被全部可见历史及当前开场明确解决的澄清线索。仅从当前卡片private_experiences或occurred_events选取可由来访者自然说明的事实/感受。不要把已在过去会话中明确解决的问题重新当作未知。criterion描述如何从追问后的公开互动判断是否准确澄清，不能预设诊断或唯一问法。source_field/source_index必须准确指向卡片原项，不编造私有事实。你不接触任何候选答卷或条件名称。",
    }
    jid = queue.enqueue(
        "clarification-targets:" + checkpoint["id"],
        "pre-response clarification target designer",
        context,
        SCHEMA,
    )
    job = execute_job(queue, api, jid, root / "experiments/api_raw")
    targets = []
    for index, item in enumerate(job["result"]["targets"], 1):
        field, position = item["source_field"], item["source_index"]
        if position >= len(card[field]):
            raise APIStopped("clarification target refers to a nonexistent private-card item")
        targets.append(
            {
                "id": checkpoint["id"] + f"-T{index}",
                "criterion": item["criterion"],
                "known_state": card[field][position],
                "source_field": field,
                "source_index": position,
            }
        )
    saved = {
        "target_ids": [t["id"] for t in targets],
        "targets": targets,
        "private_current": {
            "profile": profile,
            "current_card": card,
            "disclosure_policy": "仅依据当前设定自然回应。不要主动倾倒全部线索；相关开放追问才能适量披露。不因咨询师断言而赞同未知因果，不编造卡片之外的事实。",
        },
        "source_sha256": source_sha,
        "target_job_id": jid,
        "target_result_sha256": job["result_sha256"],
        "status": "predefined_synthetic_targets_not_clinical_truth",
        "protocol": "target-v2",
    }
    review_schema = {
        "type": "object",
        "required": ["approved", "issues"],
        "properties": {
            "approved": {"type": "boolean"},
            "issues": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }
    review_context = {
        "public_history": context["previous_public_sessions"],
        "current_public": context["current_public"],
        "current_card": card,
        "targets": targets,
        "instruction": "独立审核预定义澄清目标：是否有卡片原项依据、是否仍未在可见历史/当前开场解决、是否有明确可观察的达成条件，且不要求唯一话术或诊断。没有未解线索不能强行批准；拒绝给具体issues。你不接触候选答卷。",
    }
    review_id = queue.enqueue(
        "clarification-target-review:" + checkpoint["id"],
        "independent pre-response target reviewer",
        review_context,
        review_schema,
    )
    review = execute_job(queue, api, review_id, root / "experiments/api_raw")
    write(
        root / "experiments/target_reviews" / (checkpoint["id"] + ".json"),
        {"job_id": review_id, "result_sha256": review["result_sha256"], "result": review["result"]},
    )
    if not review["result"]["approved"]:
        raise APIStopped(
            "predefined clarification targets rejected; inspect saved review before generating answers"
        )
    saved["review_job_id"], saved["review_result_sha256"] = review_id, review["result_sha256"]
    write(path, saved)
    return saved
