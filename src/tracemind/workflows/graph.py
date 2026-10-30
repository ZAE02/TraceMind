"""Durable LangGraph workflow. Agent execution is always external.

Invoke with a stable thread_id; each interrupt yields a job request. Dispatch that
request externally, then invoke the graph with Command(resume=result). A persistent
checkpointer and memory store are both required for production durability.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from typing import Any, Iterator, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from tracemind.memory import MemoryRevision, MemoryStore, validate_revision


class WorkflowState(TypedDict, total=False):
    subject_id: str
    input: dict[str, Any]
    allowed_evidence_ids: list[str]
    extracted: dict[str, Any]
    previous: Optional[dict[str, Any]]
    retrieved: list[dict[str, Any]]
    conflicts: dict[str, Any]
    proposal: dict[str, Any]
    revision: dict[str, Any]
    response: Any
    model: str
    reasoning: str


def initial_state(
    subject_id: str,
    input: dict[str, Any],
    allowed_evidence_ids: list[str],
    *,
    model: str = "gpt-6.1-terra",
    reasoning: str = "medium",
) -> WorkflowState:
    if not subject_id:
        raise ValueError("subject_id is required")
    return {
        "subject_id": subject_id,
        "input": input,
        "allowed_evidence_ids": allowed_evidence_ids,
        "model": model,
        "reasoning": reasoning,
    }


def _job(stage: str, state: WorkflowState, payload: dict[str, Any]) -> Any:
    # The caller persists this request in its job queue and deduplicates by job_id.
    request = {
        "purpose": stage,
        "role": f"TraceMind {stage} agent",
        "allowed_context": {"subject_id": state["subject_id"], **payload},
        "output_schema": (
            {
                "type": "object",
                "required": ["evidence", "statements"],
                "properties": {
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "statements": {"type": "array", "items": {"type": "object"}},
                },
            }
            if stage == "revise"
            else {"type": "object"}
        ),
        "model": state.get("model", "gpt-6.1-terra"),
        "reasoning": state.get("reasoning", "medium"),
    }
    if request["reasoning"] == "provider-default":
        from .schemas import stage_contract

        schema, instruction = stage_contract(stage)
        request["output_schema"] = schema
        request["allowed_context"]["instruction"] = instruction
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    request["job_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    result = interrupt(request)
    if not isinstance(result, dict):
        raise ValueError(f"{stage} result must be an object")
    return result


def build_workflow(
    store: MemoryStore,
    checkpointer: Any,
    retrieve_evidence=None,
    tokenizer=None,
    resolve_evidence=None,
):
    if checkpointer is None:
        raise ValueError("a checkpointer is required for resumable jobs")
    from tracemind.memory import PostgresMemoryStore

    if isinstance(store, PostgresMemoryStore) and (
        retrieve_evidence is None or tokenizer is None or resolve_evidence is None
    ):
        raise ValueError("production memory requires retrieval and a tokenizer")
    if tokenizer is None:

        class CharacterTokenizer:
            def encode_tokens(self, text):
                return list(text)

            def decode_tokens(self, tokens):
                return "".join(tokens)

        tokenizer = CharacterTokenizer()
    from tracemind.experiments.context import pack_context
    from tracemind.memory.context import apply_patch, project_memory

    def projection(value, budget=4096):
        return project_memory(
            MemoryRevision.from_dict(value) if value else None, tokenizer, budget=budget
        )

    def historical_payload(state):
        packed = pack_context(
            tokenizer, state["retrieved"], projection(state["previous"], budget=3500)
        )
        return {"history": packed["history"], "history_tokens": packed["history_tokens"]}

    def extract(state: WorkflowState):
        return {
            "extracted": _job(
                "extract",
                state,
                {"input": state["input"], "allowed_evidence_ids": state["allowed_evidence_ids"]},
            )
        }

    def retrieve(state: WorkflowState):
        history = store.history(state["subject_id"])
        evidence = retrieve_evidence(state) if retrieve_evidence is not None else []
        if any(item.get("id") not in state["allowed_evidence_ids"] for item in evidence):
            raise ValueError("retrieval returned evidence outside allowed IDs")
        # Checkpoints contain portable JSON values, not Python Enum objects that
        # require custom msgpack type registration when restored by LangGraph.
        previous = json.loads(json.dumps(history[-1].to_dict())) if history else None
        if state.get("reasoning") == "provider-default":
            evidence = pack_context(tokenizer, evidence, projection(previous, budget=3500))[
                "history"
            ]["evidence"]
        return {"previous": previous, "retrieved": evidence}

    def conflict(state: WorkflowState):
        return {
            "conflicts": _job(
                "conflict",
                state,
                {
                    "extracted": state["extracted"],
                    **(
                        historical_payload(state)
                        if state.get("reasoning") == "provider-default"
                        else {
                            "previous": projection(state["previous"]),
                            "retrieved": state["retrieved"],
                        }
                    ),
                },
            )
        }

    def revise(state: WorkflowState):
        return {
            "proposal": _job(
                "revise",
                state,
                {
                    "extracted": state["extracted"],
                    **(
                        historical_payload(state)
                        if state.get("reasoning") == "provider-default"
                        else {
                            "previous": projection(state["previous"]),
                            "retrieved": state["retrieved"],
                        }
                    ),
                    "conflicts": state["conflicts"],
                },
            )
        }

    def validate(state: WorkflowState):
        previous = MemoryRevision.from_dict(state["previous"]) if state["previous"] else None
        proposal = state["proposal"]
        if resolve_evidence is not None:
            proposal = resolve_evidence(proposal, state)
        revision = apply_patch(
            state["subject_id"], previous, proposal, set(state["allowed_evidence_ids"])
        )
        allowed = set(state["allowed_evidence_ids"])
        if previous:
            allowed.update(e.id for e in previous.evidence)
        validate_revision(revision, previous, allowed_evidence_ids=allowed)
        return {"revision": json.loads(json.dumps(revision.to_dict()))}

    def persist(state: WorkflowState):
        store.append(MemoryRevision.from_dict(state["revision"]))
        return {}

    def respond(state: WorkflowState):
        packed = pack_context(tokenizer, [], projection(state["revision"]), current=state["input"])
        return {
            "response": _job(
                "respond",
                state,
                {
                    "history": packed["history"],
                    "history_tokens": packed["history_tokens"],
                    "input": state["input"],
                },
            )
        }

    graph = StateGraph(WorkflowState)
    for name, node in (
        ("extract", extract),
        ("retrieve", retrieve),
        ("conflict", conflict),
        ("revise", revise),
        ("validate", validate),
        ("persist", persist),
        ("respond", respond),
    ):
        graph.add_node(name, node)
    for left, right in (
        (START, "extract"),
        ("extract", "retrieve"),
        ("retrieve", "conflict"),
        ("conflict", "revise"),
        ("revise", "validate"),
        ("validate", "persist"),
        ("persist", "respond"),
        ("respond", END),
    ):
        graph.add_edge(left, right)
    return graph.compile(checkpointer=checkpointer)


@contextmanager
def postgres_checkpointer(connection_string: str) -> Iterator[Any]:
    """Open a production checkpointer; caller owns the context lifetime."""
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(connection_string) as saver:
        saver.setup()
        yield saver


def retrieval_callback(retriever):
    """Adapt the real historical Retriever using explicit query and temporal cutoff."""
    from datetime import datetime

    def retrieve(state):
        options = state["input"].get("retrieval")
        if not isinstance(options, dict):
            raise ValueError("input.retrieval requires query, cutoff and as_of")
        cutoff = options["cutoff"]
        if not isinstance(cutoff, list) or len(cutoff) != 2:
            raise ValueError("retrieval cutoff must be [session, turn]")
        as_of = datetime.fromisoformat(options["as_of"].replace("Z", "+00:00"))
        ranked = retriever.search(options["query"], state["subject_id"], tuple(cutoff), as_of)
        allowed = set(state["allowed_evidence_ids"])
        return [
            {
                "id": r.evidence.id,
                "session": r.evidence.session,
                "turn": r.evidence.turn,
                "role": r.evidence.role,
                "text": r.evidence.text,
                "score": r.score,
                "occurred_at": r.evidence.occurred_at.isoformat(),
                "observed_at": r.evidence.observed_at.isoformat(),
                "metadata": r.evidence.metadata,
                "truncated": r.truncated,
            }
            for r in ranked
            if r.evidence.id in allowed
        ]

    return retrieve


def authoritative_evidence_resolver(repository):
    from datetime import datetime

    from tracemind.memory.context import canonicalize_evidence_patch

    def resolve(patch, state):
        options = state["input"]["retrieval"]
        as_of = datetime.fromisoformat(options["as_of"].replace("Z", "+00:00"))
        visible = repository.visible(state["subject_id"], tuple(options["cutoff"]), as_of)
        records = {record.id: record for record in visible}
        exposed = {item["id"] for item in state["retrieved"]}
        return canonicalize_evidence_patch(
            patch,
            records,
            exposed & set(state["allowed_evidence_ids"]),
            case_id=state["subject_id"],
        )

    return resolve
