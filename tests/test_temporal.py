from datetime import datetime, timezone

from tracemind.retrieval.pipeline import temporal_coverage
from tracemind.storage import EvidenceRecord


def test_temporal_baseline_keeps_early_and_recent_client_anchors():
    now = datetime.now(timezone.utc)
    records = [
        EvidenceRecord(str(i), "c", i, 1, "client", f"event{i}", now, now) for i in range(1, 5)
    ]
    selected = temporal_coverage(["2", "3", "1"], records, limit=3)
    assert [x.id for x in selected] == ["1", "4", "2"]
