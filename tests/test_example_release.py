import pytest

from tracemind.benchmark.builder import write
from tracemind.benchmark.export import export_development
from tracemind.jobs import digest


def test_gemini_release_requires_verified_review_source(tmp_path):
    root, destination = tmp_path / "dataset", tmp_path / "public"
    write(root / "dataset.json", {"namespace": "gemini_v1"})
    rows = [
        {
            "id": str(i),
            "role": "client" if i % 2 == 0 else "counselor",
            "text": "fictional",
            "job_id": "source",
            "result_sha256": "hash",
        }
        for i in range(16)
    ]
    session = {
        "case_id": "case",
        "session": 1,
        "split": "development",
        "complete": True,
        "utterances": rows,
    }
    write(root / "dialogues/case/S01.json", session)
    result = {"approved": True, "issues": []}
    write(
        root / "dialogue_reviews/case_S01.json",
        {
            **result,
            "dialogue_sha256": digest(session),
            "job_id": "review",
            "result_sha256": digest(result),
        },
    )
    with pytest.raises(ValueError, match="actual review jobs"):
        export_development(root, destination)

    class Queue:
        valid = False

        def get(self, ident):
            return {
                "status": "completed",
                "result_sha256": digest(result) if self.valid else "forged",
                "request": {
                    "role": "independent session quality reviewer",
                    "allowed_context": {
                        "dialogue": [
                            {key: row[key] for key in ("id", "role", "text")} for row in rows
                        ]
                    },
                },
            }

    queue = Queue()
    with pytest.raises(ValueError, match="provenance mismatch"):
        export_development(root, destination, queue=queue)
    assert not list(destination.glob("**/S01.json"))
    queue.valid = True
    assert export_development(root, destination, queue=queue)["exported_sessions"] == 1
