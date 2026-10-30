import json

import pytest

from tracemind.evaluation.protocol import DIMENSIONS, ERRORS, QUALITY_SCHEMA, RUBRIC, VERSION
from tracemind.experiments.protocol import freeze_protocol
from tracemind.experiments.protocol import test_review_ready as gate_ready
from tracemind.jobs import digest


class Queue:
    def __init__(self, jobs):
        self.jobs = jobs

    def get(self, job_id):
        return self.jobs[job_id]


def calibration_fixture(root, model="gpt-6.1-terra", reasoning="medium"):
    (root / "checkpoints").mkdir()
    refs, jobs = [], {}
    errors = {
        key: {"occurred": False, "quote": None, "evidence_ids": [], "explanation": ""}
        for key in ERRORS
    }
    candidate = {
        "scores": {k: 2 for k in DIMENSIONS},
        "errors": errors,
        "tentative_exploration": False,
        "rationale": "fixture",
    }
    result = {"a": candidate, "b": candidate, "preference": "tie", "confidence": "low"}
    for i in range(12):
        cp = {
            "id": f"cp{i}",
            "group_id": f"g{i % 3}",
            "split": "calibration",
            "previous_sessions": [],
            "current_dialogue": [],
            "latest_client": {"id": f"u{i}", "text": "current"},
        }
        cp["public_sha256"] = digest(cp)
        (root / "checkpoints" / (cp["id"] + ".json")).write_text(json.dumps(cp), encoding="utf-8")
        context = {
            "public_history": [],
            "current": {"current_dialogue": [], "latest_client": cp["latest_client"]},
            "response_a": "a",
            "response_b": "b",
            "instruction": RUBRIC,
            "rubric_version": VERSION,
        }

        def make(purpose, actor):
            request = {
                "purpose": purpose,
                "role": "blinded quality reviewer",
                "allowed_context": context,
                "output_schema": QUALITY_SCHEMA,
                "model": model,
                "reasoning": reasoning,
                "parent_id": None,
            }
            jid = digest(request)
            jobs[jid] = {
                "id": jid,
                "request": request,
                "status": "completed",
                "result": result,
                "result_sha256": digest(result),
                "provenance": {
                    "model": model,
                    "reasoning": reasoning,
                    **(
                        {"provider": "openai-compatible", "execution_id": actor}
                        if reasoning == "provider-default"
                        else {"agent_task": actor}
                    ),
                },
            }
            return jid

        original = make("quality:" + str(i), "actor-a-" + str(i))
        repeat = make("calibration-repeat:" + original, "actor-b-" + str(i))
        refs.append(
            {"checkpoint_id": cp["id"], "quality_job_id": original, "repeat_job_id": repeat}
        )
    queue = Queue(jobs)
    queue.model, queue.reasoning = model, reasoning
    return queue, refs


def test_api_calibration_requires_distinct_actual_execution_ids(tmp_path):
    queue, refs = calibration_fixture(tmp_path, "gemini-3.8-flash", "provider-default")
    freeze_protocol(tmp_path, queue, refs)
    assert gate_ready(tmp_path, queue)
    first, repeat = (queue.jobs[refs[0][key]] for key in ("quality_job_id", "repeat_job_id"))
    repeat["provenance"]["execution_id"] = first["provenance"]["execution_id"]
    with pytest.raises(ValueError, match="independent"):
        gate_ready(tmp_path, queue)


def test_freeze_and_recheck_checkpoint_and_jobs(tmp_path):
    queue, refs = calibration_fixture(tmp_path)
    assert not gate_ready(tmp_path, queue)
    assert freeze_protocol(tmp_path, queue, refs)["status"] == "model_reviewed_not_human_gold"
    assert gate_ready(tmp_path, queue)
    path = tmp_path / "checkpoints" / "cp0.json"
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["latest_client"]["text"] = "modified"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        gate_ready(tmp_path, queue)


def test_rejects_same_reviewer_and_non_calibration(tmp_path):
    queue, refs = calibration_fixture(tmp_path)
    a, b = queue.jobs[refs[0]["quality_job_id"]], queue.jobs[refs[0]["repeat_job_id"]]
    b["provenance"]["agent_task"] = a["provenance"]["agent_task"]
    with pytest.raises(ValueError, match="independent"):
        freeze_protocol(tmp_path, queue, refs)
    b["provenance"]["agent_task"] = "other"
    path = tmp_path / "checkpoints" / "cp0.json"
    cp = json.loads(path.read_text(encoding="utf-8"))
    cp["split"] = "test"
    cp["public_sha256"] = digest({k: v for k, v in cp.items() if k != "public_sha256"})
    path.write_text(json.dumps(cp), encoding="utf-8")
    with pytest.raises(ValueError, match="only calibration"):
        freeze_protocol(tmp_path, queue, refs)


def test_requires_twelve_examples_and_three_groups(tmp_path):
    queue, refs = calibration_fixture(tmp_path)
    with pytest.raises(ValueError, match="12"):
        freeze_protocol(tmp_path, queue, refs[:11])
    for i in range(12):
        path = tmp_path / "checkpoints" / f"cp{i}.json"
        cp = json.loads(path.read_text(encoding="utf-8"))
        cp["group_id"] = "g0"
        cp["public_sha256"] = digest({k: v for k, v in cp.items() if k != "public_sha256"})
        path.write_text(json.dumps(cp), encoding="utf-8")
    with pytest.raises(ValueError, match="three"):
        freeze_protocol(tmp_path, queue, refs)


def test_runner_does_not_enqueue_test_quality_before_freeze(tmp_path):
    from tracemind.experiments.runner import ExperimentRunner
    from tracemind.memory import InMemoryMemoryStore

    class Tokenizer:
        def encode_tokens(self, text):
            return list(text)

        def decode_tokens(self, tokens):
            return "".join(tokens)

    class Repo:
        def visible(self, *args):
            return []

    class Retriever:
        def search(self, *args, **kwargs):
            return []

    class PendingReviews:
        def __init__(self):
            self.jobs = {}
            self.purposes = []

        def enqueue(self, purpose, role, context, schema, parent_id=None):
            self.purposes.append(purpose)
            key = str(len(self.jobs))
            if purpose.startswith("understanding:"):
                self.jobs[key] = {
                    "status": "completed",
                    "result": {"understanding": "fixture"},
                    "result_sha256": "fixture",
                }
            elif purpose.startswith("answer:"):
                self.jobs[key] = {
                    "status": "completed",
                    "result": {"text": "fixture"},
                    "result_sha256": "fixture",
                }
            else:
                self.jobs[key] = {"status": "pending"}
            return key

        def get(self, key):
            return self.jobs[key]

    (tmp_path / "checkpoints").mkdir()
    cp = {
        "id": "cp",
        "case_id": "case",
        "group_id": "g",
        "split": "test",
        "session": 2,
        "previous_sessions": [],
        "current_dialogue": [],
        "latest_client": {"id": "u", "text": "hello"},
    }
    (tmp_path / "checkpoints" / "cp.json").write_text(json.dumps(cp), encoding="utf-8")
    queue = PendingReviews()
    result = ExperimentRunner(
        tmp_path, queue, Repo(), Retriever(), Tokenizer(), InMemoryMemoryStore()
    ).advance()
    assert result["waiting"] > 0
    assert not any(
        p.startswith("quality:") or p.startswith("understanding-quality:") for p in queue.purposes
    )


def test_rubric_change_requires_new_run(tmp_path, monkeypatch):
    import tracemind.experiments.protocol as protocol

    queue, refs = calibration_fixture(tmp_path)
    freeze_protocol(tmp_path, queue, refs)
    monkeypatch.setattr(protocol, "RUBRIC", "changed rubric")
    with pytest.raises(ValueError, match="new run directory"):
        gate_ready(tmp_path, queue)
