import runpy
from pathlib import Path

import pytest

from tracemind.benchmark.builder import read, write
from tracemind.jobs import digest

prepare = runpy.run_path(str(Path(__file__).parents[1] / "scripts/repair_rejected_session.py"))[
    "prepare"
]


def fixture(root, split="development"):
    case = "gemini_v1_G003-T4"
    session = {"split": split, "utterances": [{"id": n} for n in range(16)], "complete": True}
    path = root / "dialogues" / case / "S06.json"
    review = root / "dialogue_reviews" / f"{case}_S06.json"
    write(path, session)
    write(review, {"approved": False, "dialogue_sha256": digest(session), "issues": ["setting"]})
    write(root / "checkpoints/CP3.json", {"immutable": True})
    return path, review


def test_repair_preserves_rejection_prefix_checkpoint_and_blocks_repeat(tmp_path):
    root, archive = tmp_path / "data", tmp_path / "archive"
    path, review = fixture(root)
    original = read(path)
    prepare(root, archive)
    assert read(archive / "session.json") == original
    assert read(archive / "review.json")["approved"] is False
    assert read(path)["utterances"] == original["utterances"][:6]
    assert not read(path)["complete"] and not review.exists()
    assert read(root / "checkpoints/CP3.json") == {"immutable": True}
    # Re-introducing a rejection cannot trigger an unlimited repair loop.
    write(review, {"approved": False, "dialogue_sha256": digest(read(path))})
    with pytest.raises(ValueError, match="already prepared"):
        prepare(root, archive)


def test_repair_rejects_test_data_before_mutation(tmp_path):
    path, review = fixture(tmp_path / "data", split="test")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="requires rejected"):
        prepare(tmp_path / "data", tmp_path / "archive")
    assert path.read_bytes() == before and review.exists()


def test_calibration_repair_only_before_freeze(tmp_path):
    root = tmp_path / "data"
    path, review = fixture(root, split="calibration")
    write(root / "protocol/frozen.json", {"frozen": True})
    before = path.read_bytes()
    with pytest.raises(ValueError, match="frozen"):
        prepare(root, tmp_path / "archive")
    assert path.read_bytes() == before and review.exists()
