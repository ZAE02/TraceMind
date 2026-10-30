from tracemind.benchmark.builder import read
from tracemind.diagnostics import record_failure


def test_failure_location_never_serializes_exception_message_or_sensitive_locals(tmp_path):
    sensitive = "sk-test-private-value"
    try:
        raise ValueError(sensitive)
    except ValueError as error:
        record_failure(tmp_path / "failure.json", error)
    text = (tmp_path / "failure.json").read_text("utf-8")
    result = read(tmp_path / "failure.json")
    assert sensitive not in text and "raise ValueError" not in text
    assert result["error_type"] == "ValueError"
    assert result["frames"][-1]["function"].startswith("test_failure_location")
