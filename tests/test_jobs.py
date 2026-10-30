import os
from uuid import uuid4

import pytest

from tracemind.jobs import JobQueue, digest, validate_job_result


def test_canonical_hash_is_stable():
    assert digest({"a": 1, "b": [2]}) == digest({"b": [2], "a": 1})
    assert digest({"a": 1}) != digest({"a": 2})


def test_queue_schema_provenance_and_idempotence_opt_in():
    dsn = os.environ.get("TRACEMIND_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set TRACEMIND_TEST_POSTGRES_DSN to test PostgreSQL")
    queue = JobQueue(dsn)
    queue.setup()
    context = {"test_id": str(uuid4())}
    schema = {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}}
    job = queue.enqueue("test", "test", context, schema)
    assert queue.enqueue("test", "test", context, schema) == job
    provenance = {
        "model": "gpt-6.1-terra",
        "reasoning": "medium",
        "agent_task": "actual-test-task-" + str(uuid4()),
    }
    with pytest.raises(Exception):
        queue.complete(job, {"value": "bad"}, provenance)
    with pytest.raises(ValueError):
        queue.complete(job, {"value": 1}, {**provenance, "agent_task": ""})
    queue.complete(job, {"value": 1}, provenance)
    queue.complete(job, {"value": 1}, provenance)
    with pytest.raises(ValueError):
        queue.complete(job, {"value": 2}, provenance)
    with pytest.raises(ValueError):
        queue.complete(job, {"value": 1}, {**provenance, "agent_task": "different"})


def test_schema_validation_rejects_invalid_results():
    from jsonschema import SchemaError, ValidationError

    schema = {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }
    validate_job_result(schema, {"answer": "ok"})
    with pytest.raises(ValidationError):
        validate_job_result(schema, {"answer": 3})
    with pytest.raises(SchemaError):
        validate_job_result({"type": "not-a-type"}, {})


def test_api_jobs_preserve_model_and_actual_provider_identity_opt_in():
    dsn = os.environ.get("TRACEMIND_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set TRACEMIND_TEST_POSTGRES_DSN to test PostgreSQL")
    queue = JobQueue(dsn, model="gemini-3.8-flash", reasoning="provider-default")
    queue.setup()
    job = queue.enqueue("api-test", "client", {"run": str(uuid4())}, {"type": "object"})
    provenance = {
        "model": "gemini-3.8-flash",
        "reasoning": "provider-default",
        "provider": "openai-compatible",
        "execution_id": str(uuid4()),
    }
    with pytest.raises(ValueError, match="original job request"):
        queue.complete(job, {}, {**provenance, "model": "another-model"})
    queue.complete(job, {}, provenance)
    queue.complete(job, {}, provenance)
    saved = queue.get(job)
    assert saved["request"]["model"] == "gemini-3.8-flash"
    assert "agent_task" not in saved["provenance"]


def test_actor_scope_and_role_isolation_opt_in():
    dsn = os.environ.get("TRACEMIND_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set TRACEMIND_TEST_POSTGRES_DSN to test PostgreSQL")
    queue = JobQueue(dsn)
    queue.setup()
    run = str(uuid4())
    actor = {"model": "gpt-6.1-terra", "reasoning": "medium", "agent_task": "test-agent-" + run}
    schema = {"type": "object"}
    scope = f"trajectory:{run}:client"
    a = queue.enqueue("one", "client", {"run": run}, schema, scope_id=scope)
    b = queue.enqueue("two", "client", {"run": run}, schema, scope_id=scope)
    other_case = queue.enqueue(
        "other", "client", {"run": run}, schema, scope_id=f"trajectory:{run}-other:client"
    )
    future_anchor = queue.enqueue(
        "anchor", "client", {"run": run}, schema, scope_id=f"anchor:{run}:group:S6"
    )
    other_role = queue.enqueue("role", "counselor", {"run": run}, schema, scope_id=scope)
    review = queue.enqueue("review", "reviewer", {"run": run}, schema)
    queue.complete(a, {}, actor)
    queue.complete(a, {}, actor)
    queue.complete(b, {}, actor)
    for job in (other_case, future_anchor, other_role, review):
        with pytest.raises(ValueError, match="different scope or role"):
            queue.complete(job, {}, actor)
        assert queue.get(job)["status"] == "pending"
    review_actor = {**actor, "agent_task": "reviewer-" + run}
    queue.complete(review, {}, review_actor)
    another_review = queue.enqueue("another-review", "reviewer", {"run": run}, schema)
    with pytest.raises(ValueError, match="different scope or role"):
        queue.complete(another_review, {}, review_actor)
    # The default scope is omitted from canonical requests, preserving legacy hashes.
    request = queue.get(another_review)["request"]
    assert "scope_id" not in request
    assert digest(request) == another_review


def test_concurrent_scope_binding_has_one_winner_opt_in():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    dsn = os.environ.get("TRACEMIND_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set TRACEMIND_TEST_POSTGRES_DSN to test PostgreSQL")
    queue = JobQueue(dsn)
    queue.setup()
    run = str(uuid4())
    actor = {"model": "gpt-6.1-terra", "reasoning": "medium", "agent_task": "race-" + run}
    jobs = [
        queue.enqueue(
            "race",
            "client",
            {"run": run, "branch": i},
            {"type": "object"},
            scope_id=f"trajectory:{run}:{i}",
        )
        for i in (0, 1)
    ]
    barrier = Barrier(2)

    def attempt(job):
        barrier.wait(timeout=10)
        try:
            queue.complete(job, {}, actor)
            return "completed"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, jobs))
    assert sorted(results) == ["completed", "rejected"]
