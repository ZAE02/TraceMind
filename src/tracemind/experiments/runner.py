"""Resumable experiments; all generated text must come from completed jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tracemind.benchmark.builder import TEXT_SCHEMA, read, write
from tracemind.evaluation.protocol import (
    ACCESS_SCHEMA,
    QUALITY_SCHEMA,
    RUBRIC,
    VERSION,
    validate_quality,
)
from tracemind.jobs import digest

from .context import pack_context

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

METHODS = ("current", "temporalrag", "summary", "on_demand", "evolution", "oracle")
INSTRUCTIONS = "根据提供的公开对话回应当前来访者。区分观察与推测，不做诊断，允许对方纠正。"


class ExperimentRunner:
    def __init__(self, root: Path, queue, repository, retriever, tokenizer, memory_store=None):
        self.root, self.queue, self.repository, self.retriever, self.tokenizer = (
            root,
            queue,
            repository,
            retriever,
            tokenizer,
        )
        self.memory_store = memory_store

    def _resolved(self, job_id):
        job = self.queue.get(job_id)
        return job if job["status"] == "completed" else None

    def _snapshot(self, checkpoint, method):
        """Build per-session memory snapshots sequentially; on-demand uses one checkpoint job."""
        if method in ("evolution", "on_demand"):
            return self._structured_snapshot(checkpoint, method)
        case = checkpoint["case_id"]
        previous = ""
        histories = checkpoint["previous_sessions"]
        if method == "on_demand":
            histories = [{"session": checkpoint["session"], "utterances": histories}]
        for session in histories:
            number = session["session"]
            path = (
                self.root
                / "experiments"
                / "snapshots"
                / method
                / case
                / (
                    f"CP{checkpoint['session']}.json"
                    if method == "on_demand"
                    else f"S{number:02}.json"
                )
            )
            public = session["utterances"]
            packed = pack_context(self.tokenizer, public, previous)
            context = {
                "history": packed["history"],
                "instruction": "从公开对话构建带不确定性的心理理解，区分事实和推测；保留证据标识，不引入未知事实。",
            }
            if method == "summary":
                context["instruction"] = "总结公开对话，并保留可追溯的证据标识；不要添加未知事实。"
            job_id = self.queue.enqueue(
                f"prepare:{method}:{case}:S{number}", "memory preparer", context, TEXT_SCHEMA
            )
            job = self._resolved(job_id)
            if job is None:
                return None
            result = {
                "job_id": job_id,
                "result_sha256": job["result_sha256"],
                "text": job["result"]["text"],
                "context_sha256": digest(context),
            }
            if path.exists() and read(path) != result:
                raise ValueError(f"snapshot changed: {path}")
            if not path.exists():
                write(path, result)
            previous = result["text"]
        return previous

    def _structured_snapshot(self, checkpoint, method):
        from tracemind.memory.context import (
            apply_patch,
            canonicalize_evidence_patch,
            project_memory,
        )

        if self.memory_store is None:
            raise ValueError("structured methods require a persistent memory store")
        case = checkpoint["case_id"]
        histories = checkpoint["previous_sessions"]
        subject = f"experiment:{method}:{case}" + (
            f":{checkpoint['id']}" if method == "on_demand" else ""
        )
        if method == "on_demand":
            sessions = [
                {
                    "session": checkpoint["session"],
                    "utterances": [row for session in histories for row in session["utterances"]],
                }
            ]
        else:
            sessions = histories
        allowed = set()
        for index, session in enumerate(sessions):
            allowed.update(row["id"] for row in session["utterances"])
            path = (
                self.root
                / "experiments"
                / "snapshots"
                / method
                / case
                / (
                    f"CP{checkpoint['session']}.json"
                    if method == "on_demand"
                    else f"S{session['session']:02}.json"
                )
            )
            history = self.memory_store.history(subject)
            if path.exists():
                snapshot = read(path)
                revisions = [
                    revision for revision in history if revision.id == snapshot["revision_id"]
                ]
                if len(revisions) != 1:
                    raise ValueError("snapshot points to a missing memory revision")
                continue
            if len(history) < index or len(history) > index + 1:
                raise ValueError("memory revision chain does not match session sequence")
            previous = history[index - 1] if index else None
            projection = project_memory(previous, self.tokenizer, budget=3500)
            packed = pack_context(self.tokenizer, session["utterances"], projection)
            from .context import serialized

            prior_ids = {e["id"] for e in projection["evidence"]}
            while True:
                visible_ids = {item["id"] for item in packed["history"]["evidence"]}
                packed["history"]["allowed_evidence_ids"] = sorted(visible_ids | prior_ids)
                if len(self.tokenizer.encode_tokens(serialized(packed["history"]))) <= 4096:
                    break
                if not packed["history"]["evidence"]:
                    raise ValueError("memory projection metadata exceeds budget")
                packed["history"]["evidence"].pop()
            context = {
                "history": packed["history"],
                "instruction": "构建可追溯的结构化记忆。只返回新增证据和新增陈述，不重复或修改已有记录；使用事件、心理状态、假设和未解决问题，标记状态、支持和反驳证据，修正须追加并引用被取代的陈述。evidence 数组只含公开发言 ID 字符串，不提供文本或时间；另返回 statements 数组。",
            }
            schema = {
                "type": "object",
                "required": ["evidence", "statements"],
                "properties": {
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "statements": {"type": "array"},
                },
            }
            job_id = self.queue.enqueue(
                f"prepare:{method}:{case}:S{session['session']}",
                "structured memory reviser",
                context,
                schema,
            )
            job = self._resolved(job_id)
            if job is None:
                return None
            proposal = job["result"]
            projected_statements = {item["id"] for item in projection["statements"]}
            for item in proposal.get("statements", []):
                if not set(item.get("supersedes", ())).issubset(projected_statements):
                    raise ValueError("patch corrects a statement outside the projection")
                if not (
                    set(item.get("support_ids", ())) | set(item.get("refute_ids", ()))
                ).issubset(visible_ids | prior_ids):
                    raise ValueError("patch references evidence outside the projection")
            records = self.repository.existing(list(visible_ids))
            exposed = {item["id"]: item for item in packed["history"]["evidence"]}
            for record in records.values():
                if record.case_id != case or record.session >= checkpoint["session"]:
                    raise ValueError("memory evidence outside historical cutoff")
                if exposed[record.id].get("text") != record.text:
                    raise ValueError("public evidence text differs from authoritative record")
            proposal = canonicalize_evidence_patch(proposal, records, visible_ids, case_id=case)
            candidate = apply_patch(subject, previous, proposal, visible_ids | prior_ids)
            if len(history) > index:
                revision = history[index]
                if (
                    candidate.evidence != revision.evidence
                    or candidate.statements != revision.statements
                ):
                    raise ValueError("stored revision does not match completed job")
            else:
                revision = candidate
                self.memory_store.append(revision)
            write(
                path,
                {
                    "revision_id": revision.id,
                    "job_id": job_id,
                    "result_sha256": job["result_sha256"],
                },
            )
        history = self.memory_store.history(subject)
        return project_memory(history[-1] if history else None, self.tokenizer)

    def _annotation(self, checkpoint):
        path = self.root / "annotations" / (checkpoint["id"] + ".json")
        public = {
            "previous_sessions": checkpoint["previous_sessions"],
            "current_dialogue": checkpoint["current_dialogue"],
            "latest_client": checkpoint["latest_client"],
        }
        fields = (
            "oracle_evidence_ids",
            "established",
            "revisions",
            "unresolved",
            "acceptable_exploration",
            "avoid_presuppositions",
        )
        schema = {
            "type": "object",
            "required": list(fields),
            "properties": {name: {"type": "array"} for name in fields},
        }
        job_id = self.queue.enqueue(
            f"annotation:{checkpoint['id']}", "public evidence annotator", public, schema
        )
        annotation_job = self._resolved(job_id)
        if annotation_job is None:
            return None
        annotation = annotation_job["result"]
        review_schema = {
            "type": "object",
            "required": ["approved"],
            "properties": {"approved": {"type": "boolean"}},
        }
        review_id = self.queue.enqueue(
            f"annotation-review:{checkpoint['id']}",
            "independent evidence reviewer",
            {"public": public, "annotation": annotation},
            review_schema,
        )
        review = self._resolved(review_id)
        if review is None:
            return None
        record = {
            **annotation,
            "status": "model_reviewed_not_human_gold",
            "oracle_approved": review["result"]["approved"],
            "annotation_job_id": job_id,
            "annotation_result_sha256": annotation_job["result_sha256"],
            "review_job_id": review_id,
            "review_result_sha256": review["result_sha256"],
        }
        if path.exists() and read(path) != record:
            raise ValueError(f"annotation changed: {path}")
        if not path.exists():
            write(path, record)
        return record

    def _evidence(self, checkpoint, method):
        if method not in ("temporalrag", "oracle"):
            return []
        case, session = checkpoint["case_id"], checkpoint["session"]
        cutoff = (session - 1, 10**9)
        as_of = datetime.now(timezone.utc)
        visible = self.repository.visible(case, cutoff, as_of)
        if checkpoint["previous_sessions"] and not visible:
            raise ValueError(f"historical evidence not indexed for {case}")
        if method == "oracle":
            annotation = self._annotation(checkpoint)
            if annotation is None or annotation.get("oracle_approved") is not True:
                return None
            ids = set(annotation["oracle_evidence_ids"])
            by_id = {e.id: e for e in visible}
            if not ids.issubset(by_id):
                raise ValueError("oracle evidence must be historically visible")
            records = [e for e in visible if e.id in ids]
        else:
            ranked = self.retriever.search(
                checkpoint["latest_client"]["text"], case, cutoff, as_of, limit=50
            )
            visible_ids = {e.id for e in visible}
            records = [r.evidence for r in ranked if r.evidence.id in visible_ids]
        return [
            {
                "id": e.id,
                "session": e.session,
                "turn": e.turn,
                "role": e.role,
                "text": e.text,
                "occurred_at": e.occurred_at.isoformat(),
                "observed_at": e.observed_at.isoformat(),
                "metadata": e.metadata,
            }
            for e in records
        ]

    def advance(self):
        waiting, completed = 0, 0
        for path in sorted((self.root / "checkpoints").glob("*.json")):
            cp = read(path)
            current = {
                "current_dialogue": cp["current_dialogue"],
                "latest_client": cp["latest_client"],
            }
            candidates = {}
            for method in METHODS:
                memory = (
                    self._snapshot(cp, method)
                    if method in ("summary", "on_demand", "evolution")
                    else ""
                )
                evidence = self._evidence(cp, method)
                if memory is None or evidence is None:
                    waiting += 1
                    continue
                packed = pack_context(
                    self.tokenizer, evidence, memory, current=current, instructions=INSTRUCTIONS
                )
                context = {
                    "history": packed["history"],
                    "current": current,
                    "instruction": INSTRUCTIONS,
                }
                understanding_instruction = (
                    "根据可见信息形成心理理解，区分事实、假设、证据与未解决问题。"
                )
                understanding_packed = pack_context(
                    self.tokenizer,
                    evidence,
                    memory,
                    current=current,
                    instructions=understanding_instruction,
                )
                understanding_schema = {
                    "type": "object",
                    "required": ["understanding"],
                    "properties": {"understanding": {"type": "string", "minLength": 1}},
                }
                understanding_id = self.queue.enqueue(
                    f"understanding:{cp['id']}:{method}",
                    "understanding analyst",
                    {
                        "history": understanding_packed["history"],
                        "current": current,
                        "instruction": understanding_instruction,
                    },
                    understanding_schema,
                )
                understanding = self._resolved(understanding_id)
                if understanding:
                    write(
                        self.root / "experiments" / "understanding" / cp["id"] / (method + ".json"),
                        {
                            "job_id": understanding_id,
                            "result_sha256": understanding["result_sha256"],
                            "result": understanding["result"],
                            "context": understanding_packed,
                        },
                    )
                    judge_id = self.queue.enqueue(
                        f"understanding-quality:{digest([cp['id'], method])}",
                        "blinded understanding reviewer",
                        {
                            "public_history": cp["previous_sessions"],
                            "current": current,
                            "candidate": understanding["result"]["understanding"],
                            "instruction": "仅依据公开对话评价理解的证据利用与更新，分别给0至4分，引用公开发言ID并解释。",
                        },
                        TASK_A_SCHEMA,
                    )
                    judge = self._resolved(judge_id)
                    if judge:
                        allowed = {
                            u["id"]
                            for session in cp["previous_sessions"]
                            for u in session["utterances"]
                        }
                        allowed.update(u["id"] for u in cp["current_dialogue"])
                        allowed.add(cp["latest_client"]["id"])
                        if not set(judge["result"]["evidence_ids"]).issubset(allowed):
                            raise ValueError("understanding reviewer cites unavailable evidence")
                        write(
                            self.root
                            / "experiments"
                            / "understanding_quality"
                            / cp["id"]
                            / (method + ".json"),
                            {
                                "job_id": judge_id,
                                "result_sha256": judge["result_sha256"],
                                "result": judge["result"],
                            },
                        )
                    else:
                        waiting += 1
                else:
                    waiting += 1
                job_id = self.queue.enqueue(
                    f"answer:{cp['id']}:{method}", "counselor", context, TEXT_SCHEMA
                )
                job = self._resolved(job_id)
                if job is None:
                    waiting += 1
                    continue
                result = {
                    "method": method,
                    "checkpoint_id": cp["id"],
                    "job_id": job_id,
                    "result_sha256": job["result_sha256"],
                    "text": job["result"]["text"],
                    "context": packed,
                }
                target = self.root / "experiments" / "candidates" / cp["id"] / (method + ".json")
                if target.exists() and read(target) != result:
                    raise ValueError(f"candidate changed: {target}")
                if not target.exists():
                    write(target, result)
                candidates[method] = result
                completed += 1
                if method in ("temporalrag", "on_demand", "evolution"):
                    from .interactions import advance_interaction

                    interaction = advance_interaction(self.root, self.queue, cp, method, result)
                    if interaction["status"] != "completed":
                        waiting += 1
            if "evolution" in candidates:
                for method in METHODS:
                    if method == "evolution" or method not in candidates:
                        continue
                    first, second = (
                        ("evolution", method)
                        if int(digest([cp["id"], method])[:2], 16) % 2
                        else (method, "evolution")
                    )
                    quality_context = {
                        "public_history": cp["previous_sessions"],
                        "current": current,
                        "response_a": candidates[first]["text"],
                        "response_b": candidates[second]["text"],
                        "instruction": RUBRIC,
                        "rubric_version": VERSION,
                    }
                    job_id = self.queue.enqueue(
                        f"quality:{digest([cp['id'], method])}",
                        "blinded quality reviewer",
                        quality_context,
                        QUALITY_SCHEMA,
                    )
                    quality = self._resolved(job_id)
                    if quality:
                        allowed = {
                            u["id"] for s in cp["previous_sessions"] for u in s["utterances"]
                        }
                        allowed.update(u["id"] for u in cp["current_dialogue"])
                        allowed.add(cp["latest_client"]["id"])
                        validate_quality(
                            quality["result"],
                            {"a": candidates[first]["text"], "b": candidates[second]["text"]},
                            allowed,
                        )
                        write(
                            self.root / "experiments" / "quality" / cp["id"] / (method + ".json"),
                            {
                                "job_id": job_id,
                                "result_sha256": quality["result_sha256"],
                                "result": quality["result"],
                                "mapping": {"a": first, "b": second},
                            },
                        )
                    else:
                        waiting += 1
                    if not quality:
                        continue
                    for side, side_method in (("a", first), ("b", second)):
                        findings = quality["result"][side]["errors"]
                        access_id = self.queue.enqueue(
                            f"access:{cp['id']}:{method}:{side_method}",
                            "access reviewer",
                            {
                                "public_history": cp["previous_sessions"],
                                "current": current,
                                "candidate_input": candidates[side_method]["context"]["history"],
                                "findings": findings,
                                "instruction": "对每项具体质量错误及其引用证据，判断该候选输入是否缺少该错误所需证据；不要重新评价质量。",
                            },
                            ACCESS_SCHEMA,
                        )
                        access = self._resolved(access_id)
                        if not access:
                            waiting += 1
                            continue
                        result_access = access["result"]
                        cited = {
                            e for finding in findings.values() for e in finding["evidence_ids"]
                        }
                        if not set(result_access["unavailable_evidence_ids"]).issubset(cited):
                            raise ValueError(
                                "access reviewer cited evidence outside quality findings"
                            )
                        if any(
                            result_access["access_limited_by_error"][kind]
                            and not findings[kind]["occurred"]
                            for kind in findings
                        ):
                            raise ValueError("access limitation requires an actual quality finding")
                        write(
                            self.root
                            / "experiments"
                            / "access"
                            / cp["id"]
                            / f"{method}--{side_method}.json",
                            {
                                "job_id": access_id,
                                "result_sha256": access["result_sha256"],
                                "result": result_access,
                                "quality_job_id": job_id,
                                "side": side,
                                "method": side_method,
                            },
                        )
        return {"completed_candidates": completed, "waiting": waiting}

    def audit(self):
        candidates = list((self.root / "experiments" / "candidates").glob("*/*.json"))
        issues = []
        for path in candidates:
            result = read(path)
            job = self.queue.get(result["job_id"])
            if job["status"] != "completed" or job["result_sha256"] != result["result_sha256"]:
                issues.append(str(path))
            from .context import serialized

            actual = len(self.tokenizer.encode_tokens(serialized(result["context"]["history"])))
            if actual > 4096 or actual != result["context"]["history_tokens"]:
                issues.append(f"budget: {path}")
        return {"candidate_count": len(candidates), "issues": issues, "queue": self.queue.stats()}
