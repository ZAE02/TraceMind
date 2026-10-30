import json

import pytest

from tracemind.api_client import APIStopped
from tracemind.experiments.api_executor import execute_job
from tracemind.jobs import digest


class Queue:
    def __init__(self, schema, model="gemini-3.8-flash"):
        request = {
            "model": model,
            "reasoning": "provider-default",
            "role": "test role",
            "allowed_context": {"visible": "only this task"},
            "output_schema": schema,
        }
        self.ident = digest(request)
        self.job = {"id": self.ident, "request": request, "status": "pending"}

    def get(self, ident):
        return self.job

    def complete(self, ident, result, provenance):
        self.job.update(
            status="completed", result=result, result_sha256=digest(result), provenance=provenance
        )


class API:
    model = "gemini-3.8-flash"

    def __init__(self, text):
        self.text, self.calls = text, 0

    def generate(self, *args, **kwargs):
        self.calls += 1
        return self.text, {"execution_id": "actual-unit-fixture"}


def test_completed_api_job_does_not_repeat_request(tmp_path):
    queue = Queue(
        {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}}
    )
    api = API(json.dumps({"value": 2}))
    assert execute_job(queue, api, queue.ident, tmp_path)["result"] == {"value": 2}
    execute_job(queue, api, queue.ident, tmp_path)
    assert api.calls == 1


def test_invalid_schema_preserves_raw_without_content_retries(tmp_path):
    queue = Queue(
        {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}}
    )
    api = API('{"value":"invalid"}')
    for _ in range(2):
        with pytest.raises(APIStopped, match="schema"):
            execute_job(queue, api, queue.ident, tmp_path)
    assert api.calls == 1 and queue.job["status"] == "pending"


def test_wrong_model_is_not_sent_to_provider(tmp_path):
    queue = Queue({"type": "object"}, model="another")
    api = API("{}")
    with pytest.raises(APIStopped, match="configuration"):
        execute_job(queue, api, queue.ident, tmp_path)
    assert api.calls == 0
