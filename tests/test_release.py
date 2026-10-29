from tracemind.benchmark.builder import write
from tracemind.benchmark.export import export_development
from tracemind.jobs import digest


def test_release_excludes_test_and_unreviewed_sessions(tmp_path):
    root = tmp_path / "private"
    for case, split, reviewed in [
        ("heldout", "test", True),
        ("draft", "development", False),
        ("example", "development", True),
    ]:
        session = {
            "case_id": case,
            "split": split,
            "session": 1,
            "complete": True,
            "utterances": [
                {
                    "id": f"u{i}",
                    "role": "client" if i % 2 else "counselor",
                    "text": "fixture",
                    "job_id": f"j{i}",
                    "result_sha256": digest({"text": "fixture"}),
                }
                for i in range(1, 17)
            ],
        }
        write(root / "dialogues" / case / "S01.json", session)
        if reviewed:
            write(
                root / "dialogue_reviews" / f"{case}_S01.json",
                {"approved": True, "dialogue_sha256": digest(session)},
            )
    destination = tmp_path / "release"
    result = export_development(root, destination)
    assert result["exported_sessions"] == 1
    assert (destination / "example/S01.json").exists()
    assert not (destination / "heldout").exists() and not (destination / "draft").exists()
