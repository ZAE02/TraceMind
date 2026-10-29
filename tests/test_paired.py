import pytest

from tracemind.evaluation.metrics import summarize
from tracemind.evaluation.paired import paired_report


def test_repeated_candidate_judgments_not_pooled_across_contrasts():
    rows = []
    for other in ("summary", "temporalrag"):
        for group, cp in [("g1", "a"), ("g2", "b")]:
            for method in ("evolution", other):
                rows.append(
                    {
                        "comparison_id": "|".join(sorted(("evolution", other))),
                        "method": method,
                        "checkpoint_id": cp,
                        "group_id": group,
                        "unsupported_assertion": method != "evolution",
                        "score_response_decision": 3 if method == "evolution" else 2,
                    }
                )
    with pytest.raises(ValueError, match="separately"):
        summarize(rows)
    report = paired_report(rows, repeats=50)
    assert len(report["contrasts"]) == 2
    for contrast in report["contrasts"].values():
        assert contrast["differences"]["unsupported_assertion"]["difference"] == -1
        assert contrast["differences"]["score_response_decision"]["matched_checkpoints"] == 2
        assert contrast["differences"]["score_response_decision"]["base_groups"] == 2
