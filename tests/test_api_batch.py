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
