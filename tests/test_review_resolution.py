import pytest

import tracemind.benchmark.review_resolution as module
from tracemind.benchmark.builder import read, write
from tracemind.jobs import digest


def setup(tmp_path, monkeypatch, results):
    import tracemind.experiments.api_executor as executor

    session = {
        "case_id": "gemini_v1_G004-T3",
        "session": 4,
        "split": "test",
        "utterances": [{"id": "U09", "role": "client", "text": "真实原文"}],
    }
    original = {
        "approved": False,
        "issues": ["invented quote"],
        "job_id": "original",
        "dialogue_sha256": digest(session),
    }
    root = tmp_path / "dataset"
    write(root / "dialogues" / session["case_id"] / "S04.json", session)
    write(root / "dialogue_reviews" / (module.label(session) + ".json"), original)

    class Queue:
        def __init__(self):
            self.jobs = {
                "original": {
                    "request": {
                        "allowed_context": {
                            "dialogue": session["utterances"],
                            "instruction": "old review",
                        }
                    }
                }
            }

        def get(self, key):
            return self.jobs[key]

        def enqueue(self, purpose, role, context, schema):
            request = {"purpose": purpose, "role": role, "allowed_context": context}
            key = digest(request)
            self.jobs.setdefault(key, {"status": "pending", "request": request})
            return key

    queue = Queue()
    calls = []

    def execute(q, api, job_id, directory):
        calls.append(job_id)
        result = results[len(calls) - 1]
        q.jobs[job_id].update(status="completed", result=result, result_sha256=digest(result))

    monkeypatch.setattr(executor, "execute_job", execute)
    return root, session, original, queue, calls


def test_two_independent_approvals_preserve_test_data_and_original_rejection(tmp_path, monkeypatch):
    approval = {"approved": True, "findings": [], "rationale": "no supported defects"}
    root, session, original, q, calls = setup(tmp_path, monkeypatch, [approval, approval])
    before = (root / "dialogues" / session["case_id"] / "S04.json").read_bytes()
    assert module.adjudicate(root, session, original, q, None, tmp_path / "run")
    assert module.effective_approval(root, session, q)
    assert len(set(calls)) == 2
    assert all("invented quote" not in str(q.get(k)["request"]) for k in calls)
    assert read(root / "dialogue_reviews" / (module.label(session) + ".json")) == original
    assert (root / "dialogues" / session["case_id"] / "S04.json").read_bytes() == before
    assert module.adjudicate(root, session, original, q, None, tmp_path / "run")
    assert len(calls) == 2  # Never score again until the result is favorable.
    assert not module.quarantined_cases(root)


@pytest.mark.parametrize("quote", ["not actually present", "真实原文"])
def test_any_rejection_or_fabricated_evidence_keeps_case_quarantined(tmp_path, monkeypatch, quote):
    reject = {
        "approved": False,
        "rationale": "defect",
        "findings": [{"utterance_id": "U09", "quote": quote, "reason": "contamination"}],
    }
    approve = {"approved": True, "findings": [], "rationale": "no defect"}
    root, session, original, q, calls = setup(tmp_path, monkeypatch, [reject, approve])
    assert not module.adjudicate(root, session, original, q, None, tmp_path / "run")
    assert module.quarantined_cases(root) == {session["case_id"]}
    assert not module.adjudicate(root, session, original, q, None, tmp_path / "run")
    assert len(calls) == 2
    assert not module.evidence_valid({**reject, "findings": []}, session)
    assert not module.evidence_valid({**approve, "findings": reject["findings"]}, session)


def test_resolution_invalid_if_underlying_session_changes(tmp_path, monkeypatch):
    approval = {"approved": True, "findings": [], "rationale": "no defects"}
    root, session, original, q, _ = setup(tmp_path, monkeypatch, [approval, approval])
    assert module.adjudicate(root, session, original, q, None, tmp_path / "run")
    changed = {**session, "split": "development"}
    assert not module.effective_approval(root, changed, q)


def test_unreviewed_and_quarantined_sources_cannot_be_indexed(tmp_path, monkeypatch):
    from tracemind.experiments.indexing import collect_records

    root, session, _, _, _ = setup(tmp_path, monkeypatch, [])
    # Exclusion happens before detailed source parsing, so incomplete provenance cannot slip in.
    write(root / "dataset.json", {"namespace": "gemini_v1"})
    write(root / "dialogues" / session["case_id"] / "S04.json", {**session, "complete": True})
    assert collect_records(root) == []


def test_quarantine_skips_only_affected_trajectory(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tracemind.api_batch import active_cases, next_case

    root, session, _, _, _ = setup(tmp_path, monkeypatch, [])
    assert active_cases(root, None, "G004", 4) == {
        "gemini_v1_G004-T1",
        "gemini_v1_G004-T2",
        "gemini_v1_G004-T4",
    }
    for branch in (1, 2):
        for number in range(1, 7):
            write(
                root / "dialogues" / f"gemini_v1_G004-T{branch}" / f"S{number:02}.json",
                {
                    "case_id": f"gemini_v1_G004-T{branch}",
                    "session": number,
                    "complete": True,
                    "utterances": [{}] * 16,
                },
            )
    plan = SimpleNamespace(group_id="G004", model_dump=lambda: {"group_id": "G004"})
    write(
        root / "plan_reviews/G004.json",
        {"approved": True, "plan_sha256": digest(plan.model_dump())},
    )
    assert next_case(root, SimpleNamespace(plans=lambda: [plan])) == ("G004", "gemini_v1_G004-T4")


def test_missing_transport_source_is_not_regenerated_or_approved(tmp_path):
    write(
        tmp_path / "source_quarantines/task.json",
        {
            "case_id": "gemini_v1_G012-T1",
            "status": "unresolved",
            "reason": "original wire envelope unavailable; proxy content retained",
        },
    )
    assert module.quarantined_cases(tmp_path) == {"gemini_v1_G012-T1"}
