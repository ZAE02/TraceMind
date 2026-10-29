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
    provenance = {"model": "gpt-6.1-terra", "reasoning": "medium", "agent_task": "actual-test-task"}
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
