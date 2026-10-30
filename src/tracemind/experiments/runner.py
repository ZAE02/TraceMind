"""Resumable experiments; all generated text must come from completed jobs."""

from __future__ import annotations

from datetime import timedelta
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
from .protocol import TASK_A_RUBRIC, TASK_A_SCHEMA, test_review_ready

METHODS = ("current", "temporalrag", "summary", "on_demand", "evolution", "oracle")
INSTRUCTIONS = "根据提供的公开对话自然回应当前来访者。区分观察与推测，不做诊断，允许对方纠正。不向来访者提内部ID、检索方法、实验条件或记忆维护流程。"


def persist_candidate(path, result):
    # Dataclass tuples become JSON arrays on disk/PostgreSQL. Compare canonical
    # serialized content, while still rejecting any actual content change.
    if path.exists() and digest(read(path)) != digest(result):
        raise ValueError(f"candidate changed: {path}")
    if not path.exists():
        write(path, result)


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
                    "utterances": self._evidence(checkpoint, "temporalrag"),
                }
            ]
        else:
            sessions = histories
        allowed = set()
        selected_revision = None
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
                selected_revision = revisions[0]
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
                "statement_id_prefix": f"{method}:{case}:S{session['session']}:",
                "instruction": "构建可追溯的结构化记忆。只返回新增证据和新增陈述，不重复或修改已有记录；新陈述ID必须以statement_id_prefix开头。使用事件、心理状态、假设和未解决问题，标记状态、支持和反驳证据，修正须追加并引用被取代的陈述。evidence 数组只含本次新增的公开发言 ID 字符串，不提供文本或时间；另返回 statements 数组。已有记忆证据不要重复添加。supported心理判断须有来访者陈述支持；咨询师猜测不可作已获支持的心理事实。unknown时间用null，不编造日期。",
            }
            if method == "on_demand":
                context["preparation_policy"] = "hybrid-evidence-rebuild-v2"
                context["query_focus"] = checkpoint["latest_client"]
                context["instruction"] += (
                    " query_focus仅用于选择当下相关理解方向，不是历史证据，不可将其当作历史事实来源。"
                )
            schema = {
                "type": "object",
                "required": ["evidence", "statements"],
                "properties": {
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "statements": {
                        "type": "array",
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "required": [
                                "id",
                                "kind",
                                "text",
                                "status",
                                "support_ids",
                                "refute_ids",
                                "supersedes",
                            ],
                            "properties": {
                                "id": {"type": "string", "minLength": 1},
                                "text": {"type": "string", "minLength": 1},
                                "kind": {
                                    "enum": [
                                        "event",
                                        "psychological_state",
                                        "hypothesis",
                                        "unresolved_question",
                                    ]
                                },
                                "status": {
                                    "enum": [
                                        "proposed",
                                        "supported",
                                        "challenged",
                                        "superseded",
                                        "inapplicable",
                                    ]
                                },
                                **{
                                    name: {"type": "array", "items": {"type": "string"}}
                                    for name in ("support_ids", "refute_ids", "supersedes")
                                },
                                "valid_from": {"type": ["string", "null"]},
                                "valid_to": {"type": ["string", "null"]},
                            },
                            "additionalProperties": False,
                            "anyOf": [
                                {"properties": {"support_ids": {"minItems": 1}}},
                                {"properties": {"refute_ids": {"minItems": 1}}},
                            ],
                        },
                    },
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
            selected_revision = revision
            write(
                path,
                {
                    "revision_id": revision.id,
                    "job_id": job_id,
                    "result_sha256": job["result_sha256"],
                },
            )
        return project_memory(selected_revision, self.tokenizer)

    def _annotation(self, checkpoint):
        from .annotations import advance_annotation

        return advance_annotation(self.root, self.queue, checkpoint)

    def _evidence(self, checkpoint, method):
        if method not in ("temporalrag", "oracle"):
            return []
        case, session = checkpoint["case_id"], checkpoint["session"]
        cutoff = (session - 1, 10**9)
        from .indexing import BASE_TIME

        as_of = BASE_TIME + timedelta(days=session - 1)
        visible = self.repository.visible(case, cutoff, as_of)
        expected_ids = {u["id"] for s in checkpoint["previous_sessions"] for u in s["utterances"]}
        if expected_ids != {item.id for item in visible}:
            raise ValueError(f"historical evidence index does not match frozen history for {case}")
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
            from tracemind.retrieval.models import EMBEDDING_REVISION, RERANKER_REVISION

            cache_path = self.root / "experiments/retrieval" / f"{checkpoint['id']}.json"
            signature = digest(
                {
                    "query": checkpoint["latest_client"]["text"],
                    "case": case,
                    "cutoff": cutoff,
                    "as_of": as_of.isoformat(),
                    "evidence": [
                        {"id": e.id, "text": e.text, "session": e.session, "turn": e.turn}
                        for e in visible
                    ],
                    "embedding": EMBEDDING_REVISION,
                    "reranker": RERANKER_REVISION,
                    "policy": "temporal-coverage-v1",
                }
            )
            if cache_path.exists():
                trace = read(cache_path)
                if trace.get("source_sha256") != signature:
                    raise ValueError("retrieval cache source or pinned model configuration changed")
                by_id = {record.id: record for record in visible}
                return [self._evidence_dict(by_id[ident]) for ident in trace["selected_ids"]]
            from time import perf_counter

            search_started = perf_counter()
            ranked = self.retriever.search(
                checkpoint["latest_client"]["text"], case, cutoff, as_of, limit=50
            )
            from tracemind.retrieval.pipeline import temporal_coverage

            records = temporal_coverage([r.evidence.id for r in ranked], visible)
            trace = {
                "retrieval_elapsed_seconds": perf_counter() - search_started,
                "source_sha256": signature,
                "query_sha256": digest(checkpoint["latest_client"]["text"]),
                "case_id": case,
                "cutoff": list(cutoff),
                "as_of": as_of.isoformat(),
                "ranked": [
                    {"id": r.evidence.id, "score": r.score, "truncated": r.truncated}
                    for r in ranked
                ],
                "selected_ids": [r.id for r in records],
                "policy": "earliest/latest historical client anchors, then hybrid reranked evidence; final serialization budget4096",
            }
            write(cache_path, trace)
        return [self._evidence_dict(e) for e in records]

    @staticmethod
    def _evidence_dict(e):
        return {
            "id": e.id,
            "session": e.session,
            "turn": e.turn,
            "role": e.role,
            "text": e.text,
            "occurred_at": e.occurred_at.isoformat(),
            "observed_at": e.observed_at.isoformat(),
            "metadata": e.metadata,
        }

    def advance(self, checkpoint_ids=None):
        waiting, completed = 0, 0
        for path in sorted((self.root / "checkpoints").glob("*.json")):
            cp = read(path)
            if checkpoint_ids is not None and cp["id"] not in checkpoint_ids:
                continue
            current = {
                "current_dialogue": cp["current_dialogue"],
                "latest_client": cp["latest_client"],
            }
            candidates = {}
            split = cp.get("split")
            can_review = split in ("development", "calibration") or (
                split == "test" and test_review_ready(self.root, self.queue)
            )
            review_label = (
                "development_smoke" if split == "development" else "model_reviewed_not_human_gold"
            )
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
                    if can_review:
                        judge_id = self.queue.enqueue(
                            f"understanding-quality:{digest([cp['id'], method])}",
                            "blinded understanding reviewer",
                            {
                                "public_history": cp["previous_sessions"],
                                "current": current,
                                "candidate": understanding["result"]["understanding"],
                                "instruction": TASK_A_RUBRIC,
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
                                raise ValueError(
                                    "understanding reviewer cites unavailable evidence"
                                )
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
                                    "review_status": review_label,
                                },
                            )
                        else:
                            waiting += 1
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
                persist_candidate(target, result)
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
                    if not can_review:
                        waiting += 1
                        continue
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
                                "review_status": review_label,
                            },
                        )
                    else:
                        waiting += 1
                    if not quality:
                        continue
                    if split == "calibration":
                        repeat_id = self.queue.enqueue(
                            "calibration-repeat:" + job_id,
                            "blinded quality reviewer",
                            quality_context,
                            QUALITY_SCHEMA,
                        )
                        if not self._resolved(repeat_id):
                            waiting += 1
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
                        current_ids = {u["id"] for u in cp["current_dialogue"]} | {
                            cp["latest_client"]["id"]
                        }
                        if current_ids & set(result_access["unavailable_evidence_ids"]):
                            raise ValueError(
                                "access reviewer marked universally visible current evidence as unavailable"
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
