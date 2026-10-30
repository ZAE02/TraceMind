import pytest

from tracemind.experiments.reports import clustered_difference


def test_clarification_pairs_exclude_missing_or_flagged_interactions():
    left = {"a": ("g1", 1), "b": ("g1", 0), "c": ("g2", 1)}
    right = {"a": ("g1", 0), "c": ("g2", 1), "d": ("g2", 0)}
    result = clustered_difference(left, right, repeats=100)
    assert result["matched_checkpoints"] == 2 and result["base_groups"] == 2
    assert result["difference"] == 0.5
    assert result["ci95"] == [0, 1]
    assert clustered_difference({}, right)["difference"] is None
    with pytest.raises(ValueError, match="group mismatch"):
        clustered_difference({"a": ("different", 1)}, right)
