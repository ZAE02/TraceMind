"""Role-isolated, resumable generation. No hosted model client is used here."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from tracemind.jobs import JobQueue, digest

BRANCHES = ("stable", "gradual", "fluctuating", "contextual")
TEXT_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string", "minLength": 1}},
    "required": ["text"],
    "additionalProperties": False,
}


class SessionCard(BaseModel):
    session: int = Field(ge=1, le=6)
    occurred_events: list[str]
    private_experiences: list[str]
    disclosure_constraints: list[str]


class Branch(BaseModel):
    sessions: list[SessionCard]

    @model_validator(mode="after")
    def ordered(self):
        if [s.session for s in self.sessions] != list(range(1, 7)):
            raise ValueError("each branch requires six ordered sessions")
        return self


class ScenePlan(BaseModel):
    group_id: str = Field(pattern=r"^G\d{3}$")
    theme: str
    split: str
    profile: dict
    branches: dict[str, Branch]
    shared_checkpoint_situations: dict[str, dict]

    @model_validator(mode="after")
    def structure(self):
        if self.theme not in {
            "work_evaluation",
            "interpersonal_boundaries",
            "family_care",
            "intimate_relationships",
            "self_identity",
        }:
            raise ValueError("unknown scene theme")
        if not isinstance(self.profile.get("age"), int) or self.profile["age"] < 18:
            raise ValueError("only fictional adult profiles are allowed")
        if set(self.branches) != set(BRANCHES):
            raise ValueError("four trajectories required")
        if set(self.shared_checkpoint_situations) != {"S2", "S4", "S6"}:
            raise ValueError("shared checkpoint situations required")
        if self.split not in {"development", "calibration", "test"}:
            raise ValueError("invalid split")
        return self


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class BenchmarkBuilder:
    def __init__(self, root: Path, queue: JobQueue, namespace: str | None = None):
        self.root, self.queue = root, queue
        manifest = root / "dataset.json"
        if namespace is None and manifest.exists():
            namespace = read(manifest).get("namespace")
        if namespace is not None and (not namespace or not namespace.replace("_", "").isalnum()):
            raise ValueError("namespace must contain only letters, digits and underscores")
        self.namespace = namespace

    def plans(self):
        return [
            ScenePlan.model_validate(read(p)) for p in sorted((self.root / "plans").glob("G*.json"))
        ]

    def _resolved(self, job_id):
        job = self.queue.get(job_id)
        return job if job["status"] == "completed" else None

    def _freeze(self, plan, case_id, session, history, opening):
        checkpoint = {
            "id": f"{case_id}-CP{session // 2}",
            "group_id": plan.group_id,
            "case_id": case_id,
            "split": plan.split,
            "session": session,
            "previous_sessions": history,
            "current_dialogue": [],
            "latest_client": opening,
        }
        checkpoint["public_sha256"] = digest(checkpoint)
        target = self.root / "checkpoints" / (checkpoint["id"] + ".json")
        if target.exists():
            if read(target) != checkpoint:
                raise ValueError("frozen public checkpoint changed")
        else:
            write(target, checkpoint)

    def advance(self, groups: set[str] | None = None, cases: set[str] | None = None) -> dict:
        """Schedule at most one next utterance per trajectory; import resolved jobs."""
        scheduled = materialized = 0
        for plan in self.plans():
            if groups is not None and plan.group_id not in groups:
                continue
            review_path = self.root / "plan_reviews" / (plan.group_id + ".json")
            if not review_path.exists():
                continue
            review = read(review_path)
            if review.get("approved") is not True or review.get("plan_sha256") != digest(
                plan.model_dump()
            ):
                continue
            anchors = {}
            for session in (2, 4, 6):
                situation = plan.shared_checkpoint_situations[f"S{session}"]
                context = {
                    "profile": plan.profile,
                    "current_situation": situation,
                    "instruction": "你是完全虚构的成年来访者。只写本次会谈开场的一条自然中文发言，保持具体但不揭示此前心理历程；不分析、不诊断。",
                }
                if self.namespace:
                    context["dataset_namespace"] = self.namespace
                suffix = f":{self.namespace}" if self.namespace else ""
                job_id = self.queue.enqueue(
                    f"anchor:{plan.group_id}:S{session}{suffix}",
                    "client",
                    context,
                    TEXT_SCHEMA,
                    scope_id=f"anchor:{self.namespace or 'legacy'}:{plan.group_id}:{session}",
                )
                anchors[session] = (job_id, self._resolved(job_id))
            for branch_index, branch_name in enumerate(BRANCHES, 1):
                case_id = f"{plan.group_id}-T{branch_index}"
                if self.namespace:
                    case_id = f"{self.namespace}_{case_id}"
                if cases is not None and case_id not in cases:
                    continue
                history = []
                for session in range(1, 7):
                    path = self.root / "dialogues" / case_id / f"S{session:02}.json"
                    saved = (
                        read(path)
                        if path.exists()
                        else {
                            "case_id": case_id,
                            "session": session,
                            "group_id": plan.group_id,
                            "split": plan.split,
                            "plan_sha256": digest(plan.model_dump()),
                            "utterances": [],
                            "complete": False,
                        }
                    )
                    if saved["plan_sha256"] != digest(plan.model_dump()):
                        raise ValueError(
                            "plan changed after dialogue generation; create a new dataset version"
                        )
                    rows = saved["utterances"]
                    if rows and session in (2, 4, 6):
                        self._freeze(plan, case_id, session, history, rows[0])
                    if len(rows) == 16:
                        history.append({"session": session, "utterances": rows})
                        continue
                    turn = len(rows) + 1
                    role = "client" if turn % 2 else "counselor"
                    if turn == 1 and session in anchors:
                        job_id, resolved = anchors[session]
                    else:
                        visible = {
                            "case_id": case_id,
                            "session": session,
                            "previous_sessions": history,
                            "current_dialogue": rows,
                        }
                        if self.namespace:
                            visible["dataset_namespace"] = self.namespace
                        if role == "client":
                            visible.update(
                                profile=plan.profile,
                                experienced_events=[
                                    c.model_dump()
                                    for c in plan.branches[branch_name].sessions[:session]
                                ],
                                instruction="扮演虚构来访者，只生成下一条发言。只依据已发生事件和当前会谈自然回应，不倾倒所有设定，不提研究轨迹或未来。",
                            )
                        else:
                            visible["instruction"] = (
                                "你是虚构长期咨询的咨询师。仅根据公开对话回应当前来访者，一至三句。区分事实与推测，允许来访者否定解释；不做诊断，不预设隐藏原因。"
                            )
                        parent = rows[-1]["job_id"] if rows else None
                        job_id = self.queue.enqueue(
                            f"dialogue:{case_id}:S{session}:U{turn}",
                            role,
                            visible,
                            TEXT_SCHEMA,
                            parent,
                            scope_id=f"trajectory:{case_id}:{role}",
                        )
                        resolved = self._resolved(job_id)
                    if resolved:
                        rows.append(
                            {
                                "id": f"{case_id}-S{session:02}-U{turn:02}",
                                "role": role,
                                "text": resolved["result"]["text"],
                                "job_id": job_id,
                                "result_sha256": resolved["result_sha256"],
                            }
                        )
                        saved["complete"] = len(rows) == 16
                        write(path, saved)
                        materialized += 1
                        if session in (2, 4, 6) and turn == 1:
                            self._freeze(plan, case_id, session, history, rows[0])
                    else:
                        scheduled += 1
                    break
        return {"waiting_trajectories": scheduled, "materialized_utterances": materialized}

    def audit(self, verify_jobs=True):
        plans = self.plans()
        sessions = [read(p) for p in (self.root / "dialogues").glob("*/S*.json")]
        checkpoints = [read(p) for p in (self.root / "checkpoints").glob("*.json")]
        issues = []
        ids = list(
            {
                row.get("job_id")
                for session in sessions
                for row in session["utterances"]
                if row.get("job_id")
            }
        )
        verified = self.queue.get_many(ids) if verify_jobs else {}
        actor_scopes = {}
        plan_index = {p.group_id: p for p in plans}
        session_index = {(s["case_id"], s["session"]): s for s in sessions}
        if len(session_index) != len(sessions):
            issues.append("duplicate case/session records")
        utterance_ids = [u["id"] for s in sessions for u in s["utterances"]]
        if len(set(utterance_ids)) != len(utterance_ids):
            issues.append("duplicate public utterance IDs")
        for session in sessions:
            plan = plan_index.get(session.get("group_id"))
            if (
                plan is None
                or session.get("split") != plan.split
                or session.get("plan_sha256") != digest(plan.model_dump())
            ):
                issues.append("session plan or split mismatch: " + session["case_id"])
            if session["complete"] != (len(session["utterances"]) == 16):
                issues.append("session completeness mismatch: " + session["case_id"])
            for i, row in enumerate(session["utterances"]):
                if row["role"] != ("client" if i % 2 == 0 else "counselor"):
                    issues.append("role order: " + row["id"])
                if not row.get("job_id") or not row.get("result_sha256"):
                    issues.append("missing provenance: " + row["id"])
                elif verify_jobs:
                    job_id = row["job_id"]
                    if job_id not in verified:
                        issues.append("missing source job: " + row["id"])
                        continue
                    job = verified[job_id]
                    if self.namespace:
                        scope = job["request"].get("scope_id")
                        expected = (
                            f"anchor:{self.namespace}:{session['group_id']}:{session['session']}"
                            if job["request"]["purpose"].startswith("anchor:")
                            else f"trajectory:{session['case_id']}:{row['role']}"
                        )
                        provenance = job.get("provenance") or {}
                        actor = provenance.get("agent_task") or provenance.get("execution_id")
                        if (
                            scope != expected
                            or job["request"]["allowed_context"].get("dataset_namespace")
                            != self.namespace
                        ):
                            issues.append("invalid isolation scope: " + row["id"])
                        if actor in actor_scopes and actor_scopes[actor] != scope:
                            issues.append("actor reused across information scopes: " + row["id"])
                        actor_scopes[actor] = scope
                    if (
                        job["status"] != "completed"
                        or job["result_sha256"] != row["result_sha256"]
                        or digest({"text": row["text"]}) != row["result_sha256"]
                    ):
                        issues.append("result provenance mismatch: " + row["id"])
                    if job["request"]["role"] != row["role"]:
                        issues.append("role task mismatch: " + row["id"])
                    if row["role"] == "counselor" and {
                        "experienced_events",
                        "private_experiences",
                        "profile",
                    } & set(job["request"]["allowed_context"]):
                        issues.append("private card leaked to counselor: " + row["id"])
        if len({c["id"] for c in checkpoints}) != len(checkpoints):
            issues.append("duplicate checkpoint IDs")
        for checkpoint in checkpoints:
            label = checkpoint["id"]
            if checkpoint.get("public_sha256") != digest(
                {key: value for key, value in checkpoint.items() if key != "public_sha256"}
            ):
                issues.append("checkpoint hash mismatch: " + label)
            number, case = checkpoint["session"], checkpoint["case_id"]
            if checkpoint["id"] != f"{case}-CP{number // 2}":
                issues.append("checkpoint identity mismatch: " + label)
            plan = plan_index.get(checkpoint["group_id"])
            if number not in (2, 4, 6) or plan is None or checkpoint["split"] != plan.split:
                issues.append("checkpoint stage or split mismatch: " + label)
            expected_history = []
            for previous in range(1, number):
                saved = session_index.get((case, previous))
                if saved is None or not saved["complete"]:
                    issues.append("checkpoint missing complete past session: " + label)
                    continue
                expected_history.append({"session": previous, "utterances": saved["utterances"]})
            current = session_index.get((case, number))
            if (
                checkpoint["previous_sessions"] != expected_history
                or checkpoint["current_dialogue"] != []
                or current is None
                or not current["utterances"]
                or checkpoint["latest_client"] != current["utterances"][0]
            ):
                issues.append("checkpoint public history mismatch: " + label)
        for group in plans:
            for stage in (2, 4, 6):
                copies = [
                    c["latest_client"]["text"]
                    for c in checkpoints
                    if c["group_id"] == group.group_id and c["session"] == stage
                ]
                if len(set(copies)) > 1:
                    issues.append("counterfactual current differs: " + group.group_id)
        complete = sum(s["complete"] and len(s["utterances"]) == 16 for s in sessions)
        complete_cases = sum(
            all(
                (case, number) in session_index
                and session_index[case, number]["complete"]
                and len(session_index[case, number]["utterances"]) == 16
                for number in range(1, 7)
            )
            for case in {s["case_id"] for s in sessions}
        )
        approved = sum(
            (self.root / "plan_reviews" / (p.group_id + ".json")).exists()
            and read(self.root / "plan_reviews" / (p.group_id + ".json")).get("approved") is True
            and read(self.root / "plan_reviews" / (p.group_id + ".json")).get("plan_sha256")
            == digest(p.model_dump())
            for p in plans
        )
        return {
            "plans": len(plans),
            "approved_plans": approved,
            "trajectories": len({s["case_id"] for s in sessions}),
            "complete_trajectories": complete_cases,
            "complete_sessions": complete,
            "checkpoints": len(checkpoints),
            "issues": issues,
            "complete": not issues
            and (len(plans), approved, complete_cases, complete, len(checkpoints))
            == (20, 20, 80, 480, 240),
        }
