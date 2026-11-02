from copy import deepcopy

import pytest

from tracemind.api_client import APIStopped
from tracemind.benchmark.builder import write
from tracemind.experiments.project import ProjectPipeline
from tracemind.jobs import digest


def fixture(tmp_path):
    root = tmp_path
    case = "gemini_v1_G003-T1"
    sessions = []
    jobs = {}
    for number in (1, 2):
        session = {
            "case_id": case,
            "session": number,
            "group_id": "G003",
            "split": "calibration",
            "complete": True,
            "utterances": [{"id": f"U{number}", "role": "client", "text": "visible"}],
        }
        result = {"approved": True, "issues": []}
        ident = f"job{number}"
        jobs[ident] = {"status": "completed", "result_sha256": digest(result)}
        write(root / "dialogues" / case / f"S{number:02}.json", session)
        write(
            root / "dialogue_reviews" / f"{case}_S{number:02}.json",
            {
                **result,
                "job_id": ident,
                "result_sha256": digest(result),
                "dialogue_sha256": digest(session),
            },
        )
        sessions.append(session)
    cp = {
        "id": f"{case}-CP1",
        "case_id": case,
        "group_id": "G003",
        "split": "calibration",
        "session": 2,
        "previous_sessions": [{"session": 1, "utterances": sessions[0]["utterances"]}],
        "current_dialogue": [],
        "latest_client": sessions[1]["utterances"][0],
    }
    cp["public_sha256"] = digest(cp)

    class Queue:
        def get(self, ident):
            return jobs[ident]

    return ProjectPipeline(root), Queue(), cp


def test_ready_prefix_is_independent_of_unrelated_or_future_quarantine(tmp_path):
    pipeline, queue, cp = fixture(tmp_path)
    write(
        tmp_path / "source_quarantines/future.json",
        {"status": "unresolved", "case_id": cp["case_id"], "session": 3},
    )
    assert pipeline.calibration_ready(cp, queue)
    write(
        tmp_path / "source_quarantines/current.json",
        {"status": "unresolved", "case_id": cp["case_id"], "session": 2},
    )
    assert not pipeline.calibration_ready(cp, queue)


def test_checkpoint_cannot_substitute_unreviewed_history(tmp_path):
    pipeline, queue, cp = fixture(tmp_path)
    changed = deepcopy(cp)
    changed["previous_sessions"][0]["utterances"][0]["text"] = "future or altered"
    changed["public_sha256"] = digest({k: v for k, v in changed.items() if k != "public_sha256"})
    with pytest.raises(APIStopped, match="reviewed source"):
        pipeline.calibration_ready(changed, queue)
