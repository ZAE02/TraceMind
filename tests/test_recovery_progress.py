import copy

import pytest

from tracemind import api_batch
from tracemind.benchmark.builder import read, write
from tracemind.jobs import digest


def test_recovery_preserves_immutability_and_imports_only_uncommitted_results(tmp_path, monkeypatch):
    monkeypatch.setattr(api_batch, "LOCAL", tmp_path)
    request = {"model": "gemini-3.8-flash", "reasoning": "provider-default",
               "output_schema": {"type": "object", "required": ["text"]}}
    ident = digest(request)
    result = {"text": "original"}
    provenance = {"model": request["model"], "reasoning": request["reasoning"],
                  "provider": "openai-compatible", "execution_id": "original-execution"}
    artifact = {"job_id": ident, "result": result, "provenance": provenance}
    write(tmp_path / (ident + ".json"), artifact)
    job = {"request": request, "status": "completed", "result": result,
           "result_sha256": digest(result), "provenance": provenance}

    class Queue:
        saved = {ident: job}
        imports = []

        def get_many(self, ids):
            assert ids == [ident]
            return self.saved

        def complete(self, *args):
            self.imports.append(args)

    queue = Queue()
    state = {"completed": []}
    api_batch.restore_saved_results(tmp_path, queue, state, tmp_path / "state.json")
    assert state["completed"] == [ident] and queue.imports == []
    assert read(tmp_path / "recovery_progress.json")["phase"] == "recovery_complete"
    tampered = copy.deepcopy(artifact)
    tampered["provenance"]["execution_id"] = "different"
    write(tmp_path / (ident + ".json"), tampered)
    with pytest.raises(ValueError, match="immutable"):
        api_batch.restore_saved_results(tmp_path, queue, state, tmp_path / "state.json")
    write(tmp_path / (ident + ".json"), artifact)
    queue.saved = {}
    api_batch.restore_saved_results(tmp_path, queue, state, tmp_path / "state.json")
    assert queue.imports == [(ident, result, provenance)]
    assert state["completed"] == [ident]
    (tmp_path / "api").mkdir()
    (tmp_path / "api/STOP").touch()
    with pytest.raises(api_batch.APIStopped, match="recovery paused"):
        api_batch.restore_saved_results(tmp_path, queue, state, tmp_path / "state.json")
