from pathlib import Path

import pytest

from tracemind.benchmark import builder


def test_transient_replace_lock_retries_only_local_file_operation(tmp_path, monkeypatch):
    path = tmp_path / "artifact.json"
    builder.write(path, {"old": True})
    original = Path.replace
    attempts, waits = [], []

    def replace(self, target):
        attempts.append(str(self))
        if len(attempts) <= 2:
            assert builder.read(path) == {"old": True}
            raise PermissionError("fixture transient lock")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(builder.time, "sleep", waits.append)
    builder.write(path, {"new": True})
    assert builder.read(path) == {"new": True}
    assert len(attempts) == 3 and waits == [0.05, 0.1]


def test_persistent_lock_stops_with_original_and_recovery_temp_intact(tmp_path, monkeypatch):
    path = tmp_path / "artifact.json"
    builder.write(path, {"old": True})
    attempts = []

    def replace(self, target):
        attempts.append(1)
        raise PermissionError("fixture persistent lock")

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(builder.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError):
        builder.write(path, {"new": True})
    assert len(attempts) == 6 and builder.read(path) == {"old": True}
    assert builder.read(path.with_suffix(".tmp")) == {"new": True}
