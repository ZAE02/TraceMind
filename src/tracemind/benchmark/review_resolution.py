"""Append-only review adjudication; never overwrite a rejection or test dialogue."""

from tracemind.jobs import digest

from .builder import read, write

PROTOCOL = "session-adjudication-v1-two-independent"
SCHEMA = {
    "type": "object",
    "required": ["approved", "findings", "rationale"],
    "properties": {
        "approved": {"type": "boolean"},
        "rationale": {"type": "string", "minLength": 1},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["utterance_id", "quote", "reason"],
                "properties": {
                    k: {"type": "string", "minLength": 1}
                    for k in ("utterance_id", "quote", "reason")
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def label(session):
    return f"{session['case_id']}_S{session['session']:02}"


def evidence_valid(result, session):
    rows = {row["id"]: row["text"] for row in session["utterances"]}
    findings = result.get("findings", [])
    if result.get("approved"):
        return findings == []
    return bool(findings) and all(
        f.get("utterance_id") in rows
        and bool(f.get("quote"))
        and f["quote"] in rows[f["utterance_id"]]
        for f in findings
    )


def effective_approval(root, session, queue=None):
    primary_path = root / "dialogue_reviews" / (label(session) + ".json")
    if not primary_path.exists():
        return False
    primary = read(primary_path)
    if primary.get("dialogue_sha256") != digest(session):
        return False
    if primary.get("approved") is True:
        return True
    path = root / "review_resolutions" / (label(session) + ".json")
    if not path.exists():
        return False
    value = read(path)
    decisions = value.get("decisions", [])
    if not (
        value.get("protocol") == PROTOCOL
        and value.get("session_sha256") == digest(session)
        and value.get("original_review_sha256") == digest(primary)
        and value.get("approved") is True
        and len(decisions) == 2
        and len({d["job_id"] for d in decisions}) == 2
    ):
        return False
    for decision in decisions:
        result = decision["result"]
        if not (
            result.get("approved") is True
            and evidence_valid(result, session)
            and digest(result) == decision["result_sha256"]
        ):
            return False
        if queue is not None:
            job = queue.get(decision["job_id"])
            if not (
                job["status"] == "completed"
                and job["result"] == result
                and job["result_sha256"] == decision["result_sha256"]
                and job["request"]["role"] == "independent session adjudicator"
                and job["request"]["allowed_context"]["dialogue"]
                == [{k: row[k] for k in ("id", "role", "text")} for row in session["utterances"]]
            ):
                return False
    return True


def adjudicate(root, session, original, queue, api, directory):
    """Exactly two new isolated judgments, not retries until a favorable result."""
    destination = root / "review_resolutions" / (label(session) + ".json")
    if destination.exists():
        return effective_approval(root, session, queue)
    from tracemind.experiments.api_executor import execute_job

    original_context = queue.get(original["job_id"])["request"]["allowed_context"]
    context = {k: v for k, v in original_context.items() if k != "instruction"}
    context["instruction"] = (
        "独立审查虚构成人咨询会话的自然性、人物和已发生事件一致性、角色与情境完整性。"
        "只依据本输入，不能虚构原文或要求所有私有线索都披露。"
        "存在严重具体问题才拒绝；每项findings必须给本输入中真实utterance_id、该发言的逐字quote和reason。"
        "approved=true时findings必须为空；拒绝时必须至少一项有原文依据的问题。"
        "rationale解释整体判断，不输出临床认可。这是独立审查任务，不知其他审查者结论。"
    )
    decisions = []
    for panel in (1, 2):
        job_id = queue.enqueue(
            f"{PROTOCOL}:{label(session)}:{digest(session)}:panel{panel}",
            "independent session adjudicator",
            context,
            SCHEMA,
        )
        execute_job(queue, api, job_id, directory / "adjudication_raw")
        job = queue.get(job_id)
        decisions.append(
            {
                "job_id": job_id,
                "result_sha256": job["result_sha256"],
                "result": job["result"],
                "evidence_valid": evidence_valid(job["result"], session),
            }
        )
    write(
        destination,
        {
            "protocol": PROTOCOL,
            "session_sha256": digest(session),
            "original_review_sha256": digest(original),
            "original_review_job": original["job_id"],
            "approved": all(d["evidence_valid"] and d["result"]["approved"] for d in decisions),
            "decisions": decisions,
            "human_review": "not_performed",
        },
    )
    return effective_approval(root, session, queue)


def quarantined_cases(root):
    cases = set()
    for p in (root / "dialogues").glob("*/S*.json"):
        session = read(p)
        review = root / "dialogue_reviews" / (label(session) + ".json")
        if (
            review.exists()
            and not read(review).get("approved")
            and not effective_approval(root, session)
        ):
            cases.add(session["case_id"])
    return cases
