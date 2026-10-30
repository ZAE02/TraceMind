"""Public-only annotations with independently reviewed, auditable correction attempts."""

from pathlib import Path

from tracemind.benchmark.builder import read, write
from tracemind.jobs import digest, execution_identity

FIELDS = (
    "established",
    "revisions",
    "unresolved",
    "acceptable_exploration",
    "avoid_presuppositions",
)
ITEM = {
    "type": "object",
    "required": ["text", "evidence_ids"],
    "properties": {
        "text": {"type": "string", "minLength": 1},
        "evidence_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    },
    "additionalProperties": False,
}
DRAFT_SCHEMA = {
    "type": "object",
    "required": ["oracle_evidence_ids", *FIELDS],
    "properties": {
        "oracle_evidence_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        **{field: {"type": "array", "items": ITEM} for field in FIELDS},
    },
    "additionalProperties": False,
}
REVIEW_SCHEMA = {
    "type": "object",
    "required": ["approved", "issues"],
    "properties": {
        "approved": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}


class AnnotationRejected(ValueError):
    pass


def advance_annotation(root: Path, queue, checkpoint: dict):
    name = checkpoint["id"]
    target = root / "annotations" / (name + ".json")
    public = {
        key: checkpoint[key] for key in ("previous_sessions", "current_dialogue", "latest_client")
    }
    history = {u["id"]: u for s in public["previous_sessions"] for u in s["utterances"]}
    visible = {
        **history,
        **{u["id"]: u for u in public["current_dialogue"]},
        public["latest_client"]["id"]: public["latest_client"],
    }
    public_sha = digest(public)
    if target.exists():
        saved = read(target)
        if saved.get("public_sha256") != public_sha:
            raise ValueError("annotation public history changed; create a new run")
        jobs = {}
        for prefix in ("annotation", "review"):
            job = queue.get(saved[prefix + "_job_id"])
            jobs[prefix] = job
            if (
                job["status"] != "completed"
                or job["result_sha256"] != saved[prefix + "_result_sha256"]
            ):
                raise ValueError("saved annotation provenance mismatch")
        if any(
            saved[field] != jobs["annotation"]["result"][field]
            for field in ("oracle_evidence_ids", *FIELDS)
        ):
            raise ValueError("saved annotation differs from source result")
        if not jobs["review"]["result"]["approved"] or not saved.get("oracle_approved"):
            raise ValueError("saved annotation was not approved")
        if execution_identity(jobs["annotation"]["provenance"]) == execution_identity(
            jobs["review"]["provenance"]
        ):
            raise ValueError("annotation and review require independent task contexts")
        return saved
    feedback = []
    for attempt in range(3):
        context = {
            "public_history": public,
            "previous_issues": feedback,
            "instruction": "只根据公开历史标注多种合理认识、修订、待澄清问题和应避免预设。每项绑定真实公开发言ID；不把咨询师猜测当已确认心理事实。oracle_evidence_ids只列最小相关既往会话证据，当前发言会另行提供，不把当前ID放入oracle列表。",
        }
        draft_id = queue.enqueue(
            f"annotation:{name}:attempt{attempt}",
            "public evidence annotator",
            context,
            DRAFT_SCHEMA,
        )
        draft_job = queue.get(draft_id)
        if draft_job["status"] != "completed":
            return None
        draft = draft_job["result"]
        if not set(draft["oracle_evidence_ids"]) <= set(history):
            raise ValueError("oracle IDs must come only from earlier public sessions")
        for field in FIELDS:
            for item in draft[field]:
                if not set(item["evidence_ids"]) <= set(visible):
                    raise ValueError("annotation cites unavailable evidence")
        review_context = {
            "public_history": public,
            "draft": draft,
            "instruction": "独立核对证据归属、时间边界和修订范围，允许多种合理理解。若需要修订，approved=false并逐项给出issues；不要为了通过而批准。",
        }
        review_id = queue.enqueue(
            f"annotation-review:{name}:attempt{attempt}",
            "independent public evidence reviewer",
            review_context,
            REVIEW_SCHEMA,
        )
        review = queue.get(review_id)
        if review["status"] != "completed":
            return None
        if execution_identity(draft_job["provenance"]) == execution_identity(review["provenance"]):
            raise ValueError("annotation and review require independent task contexts")
        record = {
            **draft,
            "public_sha256": public_sha,
            "status": "model_reviewed_not_human_gold",
            "oracle_approved": review["result"]["approved"],
            "annotation_job_id": draft_id,
            "annotation_result_sha256": draft_job["result_sha256"],
            "review_job_id": review_id,
            "review_result_sha256": review["result_sha256"],
            "attempt": attempt,
            "issues": review["result"]["issues"],
        }
        attempt_path = root / "annotations/attempts" / name / f"{attempt}.json"
        if attempt_path.exists() and read(attempt_path) != record:
            raise ValueError("annotation attempt is immutable")
        if not attempt_path.exists():
            write(attempt_path, record)
        if record["oracle_approved"]:
            write(target, record)
            return record
        feedback = review["result"]["issues"]
        if not feedback:
            raise ValueError("a rejected annotation needs concrete review issues")
    raise AnnotationRejected(f"{name}: three annotation attempts rejected; inspect saved attempts")
