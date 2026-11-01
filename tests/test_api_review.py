import json

import pytest

from tracemind.api_client import APIStopped
from tracemind.benchmark.api_review import review_completed_sessions
from tracemind.benchmark.builder import write
from tracemind.jobs import digest


class Queue:
    def __init__(self):
        self.jobs = {}

    def enqueue(self, purpose, role, context, schema):
        key = digest([purpose, context])
        self.jobs.setdefault(key, {"status": "pending"})
        return key

    def complete(self, key, result, provenance):
        self.jobs[key] = {"status": "completed", "result_sha256": digest(result)}

    def get(self, key):
        return self.jobs[key]


class API:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate(self, key, messages, max_tokens):
        self.calls.append(messages)
        return self.response, {"execution_id": key}


def setup(tmp_path):
    root = tmp_path / "dataset"
    plan = {
        "profile": {"age": 30},
        "branches": {
            "stable": {
                "sessions": [
                    {"session": 1, "private_experiences": ["current card"]},
                    {"session": 2, "private_experiences": ["private_future"]},
                ]
            }
        },
    }
    write(root / "plans/G001.json", plan)
    session = {
        "group_id": "G001",
        "case_id": "case-T1",
        "session": 1,
        "complete": True,
        "utterances": [
            {"id": str(i), "role": "client" if i % 2 == 0 else "counselor", "text": "fixture"}
            for i in range(16)
        ],
    }
    write(root / "dialogues/case-T1/S01.json", session)
    return root, tmp_path / "run", {}, tmp_path / "state.json"


def test_review_is_bound_to_session_and_only_current_private_card(tmp_path):
    root, directory, state, state_path = setup(tmp_path)
    queue, api = Queue(), API(json.dumps({"approved": True, "issues": []}))
    assert review_completed_sessions(root, queue, api, directory, state, state_path) == 1
    assert len(api.calls) == 1 and "private_future" not in str(api.calls)
    assert review_completed_sessions(root, queue, api, directory, state, state_path) == 1
    assert len(api.calls) == 1
    write(
        root / "dialogues/case-T1/S01.json",
        {"complete": True, "case_id": "case-T1", "session": 1, "changed": True},
    )
    with pytest.raises(APIStopped, match="changed"):
        review_completed_sessions(root, queue, api, directory, state, state_path)
    assert len(api.calls) == 1


@pytest.mark.parametrize(
    "response", ['{"approved":false,"issues":["0: contradiction"]}', "invalid json"]
)
def test_rejected_or_invalid_review_is_not_repeated_on_resume(tmp_path, response):
    root, directory, state, state_path = setup(tmp_path)
    queue, api = Queue(), API(response)
    for _ in range(2):
        with pytest.raises(APIStopped):
            review_completed_sessions(
                root, queue, api, directory, state, state_path, isolate_rejections=False
            )
    assert len(api.calls) == 1
