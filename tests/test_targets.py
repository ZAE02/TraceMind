import json

import pytest

from tracemind.api_client import APIStopped
from tracemind.benchmark.builder import write
from tracemind.experiments.targets import prepare_targets
from tracemind.jobs import digest


class Queue:
    def __init__(self):
        self.jobs = {}

    def enqueue(self, purpose, role, context, schema):
        request = {
            "model": "gemini-3.8-flash",
            "reasoning": "provider-default",
            "role": role,
            "allowed_context": context,
            "output_schema": schema,
        }
        key = digest(request)
        self.jobs.setdefault(key, {"id": key, "request": request, "status": "pending"})
        return key

    def get(self, key):
        return self.jobs[key]

    def complete(self, key, result, provenance):
        self.jobs[key].update(
            status="completed", result=result, result_sha256=digest(result), provenance=provenance
        )


class API:
    model = "gemini-3.8-flash"

    def __init__(self):
        self.calls = []

    def generate(self, key, messages, **kwargs):
        self.calls.append(messages)
        if "independent pre-response target reviewer" in messages[1]["content"]:
            return '{"approved":true,"issues":[]}', {"execution_id": key}
        return json.dumps(
            {
                "targets": [
                    {
                        "criterion": "clarify current concern",
                        "source_field": "private_experiences",
                        "source_index": 0,
                    }
                ]
            }
        ), {"execution_id": key}


def fixture(root):
    write(
        root / "plans/G001.json",
        {
            "profile": {"age": 30},
            "branches": {
                "stable": {
                    "sessions": [
                        {"session": 1, "private_experiences": ["past"]},
                        {
                            "session": 2,
                            "private_experiences": ["current truth"],
                            "occurred_events": [],
                        },
                        {"session": 3, "private_experiences": ["secret future"]},
                    ]
                }
            },
        },
    )
    return {
        "id": "cp",
        "case_id": "case-T1",
        "group_id": "G001",
        "session": 2,
        "public_sha256": "public-hash",
        "latest_client": {"text": "current"},
    }


def test_targets_use_authoritative_current_state_without_future_cards(tmp_path):
    cp = fixture(tmp_path)
    queue, api = Queue(), API()
    saved = prepare_targets(tmp_path, queue, api, cp)
    assert saved["targets"][0]["known_state"] == "current truth"
    assert "secret future" not in str(api.calls)
    assert prepare_targets(tmp_path, queue, api, cp) == saved
    assert len(api.calls) == 2


def test_cannot_define_targets_after_candidates(tmp_path):
    cp = fixture(tmp_path)
    write(tmp_path / "experiments/candidates/cp/current.json", {"text": "already answered"})
    api = API()
    with pytest.raises(APIStopped, match="after candidate"):
        prepare_targets(tmp_path, Queue(), api, cp)
    assert not api.calls
