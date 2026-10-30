from tracemind import api_batch


def test_exactly_one_batch_and_resume_completed_batch_does_not_generate(tmp_path, monkeypatch):
    calls = []

    class API:
        model = "gemini-3.8-flash"

        def models(self, batch_id):
            calls.append("models")
            return {"confirmed": True}

        def generate(self, job_id, messages, max_tokens):
            calls.append(job_id)
            return "fictional statement", {"model": self.model, "execution_id": job_id}

    class Queue:
        number = 0

        def __init__(self, *args, **kwargs):
            pass

        def setup(self):
            pass

        def pending(self, limit):
            return [
                {
                    "job_id": f"job-{self.number}",
                    "model": "gemini-3.8-flash",
                    "purpose": "dialogue:case:S1:U1",
                    "role": "counselor",
                    "allowed_context": {
                        "case_id": "case",
                        "session": 1,
                        "dataset_namespace": api_batch.NAMESPACE,
                        "instruction": "respond",
                        "previous_sessions": [],
                        "current_dialogue": [],
                    },
                }
            ]

        def complete(self, *args):
            self.number += 1

    class Builder:
        def __init__(self, *args):
            pass

        def advance(self, **kwargs):
            pass

        def audit(self):
            return {"issues": []}

    monkeypatch.setattr(api_batch, "LOCAL", tmp_path)
    monkeypatch.setattr(api_batch, "prepare", lambda root: None)
    monkeypatch.setattr(api_batch, "dsn", lambda: "not-used")
    monkeypatch.setattr(api_batch, "LimitedAPI", API)
    monkeypatch.setattr(api_batch, "JobQueue", Queue)
    monkeypatch.setattr(api_batch, "BenchmarkBuilder", Builder)
    monkeypatch.setattr(api_batch, "next_case", lambda *args: ("G001", "case"))
    result = api_batch.run_batch("one-batch", 20)
    assert result["status"] == "completed"
    assert len(result["completed"]) == 20
    assert len(calls) == 21
    result = api_batch.run_batch("one-batch", 20)
    assert result["status"] == "completed" and len(calls) == 21
    calls.clear()
    monkeypatch.setattr(
        api_batch,
        "next_case",
        lambda *args: ("G001", "case") if len([x for x in calls if x != "models"]) < 25 else None,
    )
    result = api_batch.run_batch("continuous")
    assert result["status"] == "completed" and result["limit"] is None
    assert len(result["completed"]) == 25 and len(calls) == 26
    assert result["completion_reason"] == "all_approved_scene_trajectories_generated"


def test_interrupted_import_reuses_saved_result_and_ignores_reports(tmp_path, monkeypatch):
    from tracemind.benchmark.builder import write

    directory = tmp_path / "api/batches/interrupted"
    job_id = "a" * 64
    write(
        directory / "state.json",
        {
            "batch_id": "interrupted",
            "limit": 1,
            "completed": [],
            "status": "running",
            "dataset_namespace": api_batch.NAMESPACE,
            "started_at": "2026-10-30T00:00:00+00:00",
        },
    )
    write(directory / "models.json", {"confirmed": True})
    write(directory / "audit_report.json", {"issues": []})
    write(
        directory / (job_id + ".json"),
        {
            "job_id": job_id,
            "result": {"text": "saved actual completion"},
            "provenance": {"execution_id": "original"},
        },
    )
    imported = []

    class API:
        model = "gemini-3.8-flash"

        def models(self, *args):
            raise AssertionError("must not repeat discovery")

        def generate(self, *args, **kwargs):
            raise AssertionError("must not repeat saved generation")

    class Queue:
        def __init__(self, *args, **kwargs):
            pass

        def setup(self):
            pass

        def complete(self, ident, result, provenance):
            imported.append((ident, result, provenance))

    class Builder:
        def __init__(self, *args):
            pass

        def audit(self):
            return {"issues": []}

    monkeypatch.setattr(api_batch, "LOCAL", tmp_path)
    monkeypatch.setattr(api_batch, "prepare", lambda root: None)
    monkeypatch.setattr(api_batch, "dsn", lambda: "not-used")
    monkeypatch.setattr(api_batch, "LimitedAPI", API)
    monkeypatch.setattr(api_batch, "JobQueue", Queue)
    monkeypatch.setattr(api_batch, "BenchmarkBuilder", Builder)
    result = api_batch.run_batch("interrupted", 1)
    assert result["status"] == "completed" and result["completed"] == [job_id]
    assert len(imported) == 1 and imported[0][2]["execution_id"] == "original"
