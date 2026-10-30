import copy

import pytest

from tracemind.experiments.annotations import FIELDS, advance_annotation
from tracemind.jobs import digest


class Queue:
    def __init__(self, api=False):
        self.jobs = {}
        self.api = api

    def enqueue(self, purpose, role, allowed_context, output_schema):
        key = digest([purpose, allowed_context])
        self.jobs.setdefault(key, {"status": "pending", "context": allowed_context})
        return key

    def get(self, key):
        return self.jobs[key]

    def complete(self, key, result):
        self.jobs[key].update(
            status="completed",
            result=result,
            result_sha256=digest(result),
            provenance={"provider": "openai-compatible", "execution_id": key}
            if self.api
            else {"agent_task": key},
        )


@pytest.mark.parametrize("api", [False, True])
def test_rejected_annotation_repairs_with_new_independent_jobs(tmp_path, api):
    cp = {
        "id": "cp",
        "previous_sessions": [{"utterances": [{"id": "old", "text": "past"}]}],
        "current_dialogue": [],
        "latest_client": {"id": "now", "text": "current"},
    }
    queue = Queue(api)
    assert advance_annotation(tmp_path, queue, cp) is None
    draft = {field: [] for field in FIELDS}
    draft["oracle_evidence_ids"] = ["old"]
    first = next(iter(queue.jobs))
    queue.complete(first, draft)
    assert advance_annotation(tmp_path, queue, cp) is None
    review = next(key for key, value in queue.jobs.items() if value["status"] == "pending")
    queue.complete(review, {"approved": False, "issues": ["clarify uncertainty"]})
    assert advance_annotation(tmp_path, queue, cp) is None
    repaired = next(key for key, value in queue.jobs.items() if value["status"] == "pending")
    assert repaired != first
    assert queue.jobs[repaired]["context"]["previous_issues"] == ["clarify uncertainty"]
    assert not (tmp_path / "annotations/cp.json").exists()
    queue.complete(repaired, copy.deepcopy(draft))
    assert advance_annotation(tmp_path, queue, cp) is None
    reviewer = next(key for key, value in queue.jobs.items() if value["status"] == "pending")
    queue.complete(reviewer, {"approved": True, "issues": []})
    saved = advance_annotation(tmp_path, queue, cp)
    assert saved["attempt"] == 1 and saved["oracle_approved"]
    assert advance_annotation(tmp_path, queue, cp) == saved
    queue.jobs[repaired]["result"]["oracle_evidence_ids"] = []
    with pytest.raises(ValueError, match="differs from source"):
        advance_annotation(tmp_path, queue, cp)


@pytest.mark.parametrize("oracle_ids,valid", [(["old"], True), (["now"], False)])
def test_current_annotation_citations_are_separate_from_historical_oracle(
    tmp_path, oracle_ids, valid
):
    from tracemind.benchmark.builder import read, write
    from tracemind.experiments.annotations import ANNOTATION_PROTOCOL

    cp = {
        "id": "cp",
        "previous_sessions": [{"utterances": [{"id": "old", "text": "past"}]}],
        "current_dialogue": [],
        "latest_client": {"id": "now", "text": "new concern"},
    }
    legacy = tmp_path / "annotations/attempts/cp/0.json"
    write(legacy, {"oracle_approved": False, "issues": ["legacy rejection"]})
    queue = Queue(api=True)
    advance_annotation(tmp_path, queue, cp)
    draft_id = next(iter(queue.jobs))
    draft = {field: [] for field in FIELDS}
    draft.update(
        oracle_evidence_ids=oracle_ids,
        unresolved=[{"text": "current concern", "evidence_ids": ["now"]}],
    )
    queue.complete(draft_id, draft)
    if not valid:
        with pytest.raises(ValueError, match="only from earlier public sessions"):
            advance_annotation(tmp_path, queue, cp)
        return
    advance_annotation(tmp_path, queue, cp)
    review_id = next(k for k, v in queue.jobs.items() if v["status"] == "pending")
    context = queue.jobs[review_id]["context"]
    assert context["latest_client_id"] == "now"
    assert context["evidence_scope"]["oracle_evidence_ids"] == "previous_sessions_only"
    assert context["evidence_scope"]["other_evidence_ids"] == "all_frozen_public_utterances"
    assert context["evidence_scope"]["current_provided_separately"] is True
    queue.complete(review_id, {"approved": True, "issues": []})
    saved = advance_annotation(tmp_path, queue, cp)
    assert saved["annotation_protocol"] == ANNOTATION_PROTOCOL
    assert saved["oracle_evidence_ids"] == ["old"]
    assert saved["unresolved"][0]["evidence_ids"] == ["now"]
    assert read(legacy)["issues"] == ["legacy rejection"]
    assert (legacy.parent / ANNOTATION_PROTOCOL / "0.json").exists()
