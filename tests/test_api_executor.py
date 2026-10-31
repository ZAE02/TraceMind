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


def quality_fixture():
    import copy

    from tracemind.evaluation.protocol import QUALITY_SCHEMA

    row = {
        "scores": {
            key: 3 for key in ("evidence_utilization", "understanding_update", "response_decision")
        },
        "errors": {
            key: {"occurred": False, "quote": None, "evidence_ids": [], "explanation": ""}
            for key in ("unsupported_assertion", "leading_question", "missed_progress")
        },
        "tentative_exploration": True,
        "rationale": "side explanation",
    }
    canonical = {
        "a": copy.deepcopy(row),
        "b": copy.deepcopy(row),
        "preference": "a",
        "confidence": "medium",
    }
    queue = Queue(QUALITY_SCHEMA)
    queue.job["request"]["role"] = "blinded quality reviewer"
    queue.ident = digest(queue.job["request"])
    queue.job["id"] = queue.ident
    return queue, canonical


def test_extra_global_quality_explanation_is_retained_without_changing_scores_or_retrying(tmp_path):
    from tracemind.benchmark.builder import read

    queue, canonical = quality_fixture()
    original = {**canonical, "rationale": "extra global explanation"}
    api = API(json.dumps(original))
    saved = execute_job(queue, api, queue.ident, tmp_path)
    assert saved["result"] == canonical
    metadata = saved["provenance"]["output_normalization"]
    assert metadata["unscored_metadata"] == {"rationale": original["rationale"]}
    assert metadata["original_json_sha256"] == digest(original)
    path = tmp_path / (queue.ident + ".json")
    raw_bytes = path.read_bytes()
    assert json.loads(read(path)["content"]) == original
    queue.job["status"] = "pending"  # Simulate a crash between raw save and import.
    replay = execute_job(queue, api, queue.ident, tmp_path)
    assert replay["result"] == canonical and api.calls == 1
    assert replay["provenance"]["output_normalization"] == metadata
    assert path.read_bytes() == raw_bytes


@pytest.mark.parametrize("fault", ["score", "preference", "extra", "role", "metadata_type"])
def test_metadata_adapter_never_repairs_judgments_or_other_schema_errors(tmp_path, fault):
    queue, value = quality_fixture()
    value["rationale"] = "extra explanation"
    if fault == "score":
        value["a"]["scores"]["response_decision"] = 9
    elif fault == "preference":
        value["preference"] = "unknown"
    elif fault == "extra":
        value["score_adjustment"] = 1
    elif fault == "metadata_type":
        value["rationale"] = ["not a string"]
    else:
        queue.job["request"]["role"] = "other role"
        queue.ident = digest(queue.job["request"])
    api = API(json.dumps(value))
    for _ in range(2):
        with pytest.raises(APIStopped, match="schema"):
            execute_job(queue, api, queue.ident, tmp_path)
    assert api.calls == 1 and queue.job["status"] == "pending"


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
