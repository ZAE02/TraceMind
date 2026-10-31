"""Execute persisted graph interrupts through the shared bounded-concurrency API client."""

from langgraph.types import Command

from tracemind.api_client import APIStopped
from tracemind.benchmark.builder import read, write
from tracemind.config import LOCAL, dsn
from tracemind.experiments.api_executor import execute_job
from tracemind.jobs import digest
from tracemind.memory import PostgresMemoryStore
from tracemind.storage import EvidenceRepository

from .graph import (
    authoritative_evidence_resolver,
    build_workflow,
    initial_state,
    postgres_checkpointer,
    retrieval_callback,
)


def run_api_workflow(queue, api, *, thread_id, subject_id, input, allowed_ids, models, directory):
    """Resume a real graph until completion; caller owns project-wide batch lock."""
    from tracemind.retrieval.pipeline import Retriever

    if queue.model != api.model or queue.reasoning != "provider-default":
        raise APIStopped("graph queue must use designated API model")
    request_sha = digest({"subject": subject_id, "input": input, "allowed": allowed_ids})
    progress_path = directory / "progress.json"
    progress = (
        read(progress_path)
        if progress_path.exists()
        else {"request_sha256": request_sha, "thread_id": thread_id, "jobs": {}}
    )
    if progress["request_sha256"] != request_sha or progress["thread_id"] != thread_id:
        raise APIStopped("workflow source changed; preserve checkpoint and create a new run")
    store = PostgresMemoryStore(dsn())
    repository = EvidenceRepository(dsn())
    retrieve = retrieval_callback(Retriever(repository, models))
    resolve = authoritative_evidence_resolver(repository)
    cfg = {"configurable": {"thread_id": thread_id}}
    while True:
        if (LOCAL / "api/STOP").exists():
            raise APIStopped("project paused by STOP marker")
        # Reopen the real PostgreSQL checkpointer at every transition. The graph
        # resumes exactly the saved interrupt, including after process restarts.
        with postgres_checkpointer(dsn()) as saver:
            graph = build_workflow(store, saver, retrieve, models, resolve)
            snapshot = graph.get_state(cfg)
            if snapshot.values and any(
                snapshot.values.get(key) != value
                for key, value in {
                    "subject_id": subject_id,
                    "input": input,
                    "allowed_evidence_ids": allowed_ids,
                    "model": api.model,
                    "reasoning": queue.reasoning,
                }.items()
            ):
                raise APIStopped("persisted graph source/model differs; no checkpoint relabeling")
            if not snapshot.values:
                graph.invoke(
                    initial_state(
                        subject_id, input, allowed_ids, model=api.model, reasoning=queue.reasoning
                    ),
                    cfg,
                )
                snapshot = graph.get_state(cfg)
            interrupts = [i for task in snapshot.tasks for i in task.interrupts]
            if not interrupts:
                if not snapshot.values.get("response"):
                    raise APIStopped("graph has no pending interrupt or completed response")
                history = store.history(subject_id)
                result = {
                    **progress,
                    "status": "completed_api_langgraph",
                    "response": snapshot.values["response"],
                    "memory_revisions": len(history),
                    "response_sha256": digest(snapshot.values["response"]),
                }
                write(directory / "completed.json", result)
                return result
            if len(interrupts) != 1:
                raise APIStopped("graph must expose exactly one sequential interrupt")
            request = interrupts[0].value
            if request["model"] != api.model or request["reasoning"] != queue.reasoning:
                raise APIStopped("persisted graph model differs; no provider switching")
            ident = queue.enqueue(
                request["purpose"],
                request["role"],
                request["allowed_context"],
                request["output_schema"],
                scope_id=f"graph:{thread_id}:{request['purpose']}",
            )
            if "history" in request["allowed_context"]:
                from tracemind.experiments.context import serialized

                actual = len(
                    models.encode_tokens(serialized(request["allowed_context"]["history"]))
                )
                if actual > 4096 or actual != request["allowed_context"]["history_tokens"]:
                    raise APIStopped(
                        "workflow serialized history violates recorded 4096-token budget"
                    )
            job = execute_job(queue, api, ident, directory / "api_raw")
            progress["jobs"][request["purpose"]] = {
                "job_id": ident,
                "result_sha256": job["result_sha256"],
                "execution_id": job["provenance"]["execution_id"],
            }
            write(progress_path, progress)
            graph.invoke(Command(resume=job["result"]), cfg)
