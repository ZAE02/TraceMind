from types import SimpleNamespace

import pytest

from tracemind.benchmark.builder import write
from tracemind.experiments import project


@pytest.mark.parametrize("approved", [True, False])
def test_longitudinal_smoke_reuses_completed_checkpoints_and_requires_reviews(
    tmp_path, monkeypatch, approved
):
    monkeypatch.setattr(project, "LOCAL", tmp_path)
    pipeline = project.ProjectPipeline(tmp_path / "data")
    root = pipeline.root
    write(root / "project/initial_smoke.json", {"status": "completed"})
    cps = [{"id": f"cp{i}", "case_id": "gemini_v1_G001-T1", "session": i * 2} for i in (1, 2, 3)]
    write(root / "experiments/completed/cp1.json", {"completed": True})
    for number in range(1, 7):
        write(
            root / "dialogue_reviews" / f"gemini_v1_G001-T1_S{number:02}.json",
            {"approved": approved if number == 3 else True},
        )
    called = []

    def drive(queue, api, cp, phase):
        called.append(cp["id"])
        write(root / "experiments/completed" / (cp["id"] + ".json"), {"completed": True})

    monkeypatch.setattr(pipeline, "checkpoints", lambda **kwargs: cps)
    monkeypatch.setattr(pipeline, "group_complete", lambda group: False)
    monkeypatch.setattr(pipeline, "drive_checkpoint", drive)
    api = SimpleNamespace(model="gemini-3.8-flash")
    pipeline.progress(root, None, api)
    assert called == (["cp2", "cp3"] if approved else [])
    called.clear()
    pipeline.progress(root, None, api)
    assert not called
