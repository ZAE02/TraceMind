import copy

import pytest

from tracemind.experiments.final_report import render_final_report
from tracemind.experiments.runner import METHODS


def fixture():
    data = dict(approved_plans=20, complete_trajectories=80, complete_sessions=480, checkpoints=240)
    result = dict(
        dataset=data,
        task_a={m: dict(checkpoints=60, evidence_utilization_mean=2.5, understanding_update_mean=3)
                for m in METHODS},
        model_evaluation={"contrasts": {"test": {
            "left": "evolution", "right": "temporalrag",
            "by_method": {"evolution": {"counts": {"missed_progress": 3},
                "denominators": {"missed_progress": 7}, "rates": {"missed_progress": 3/7}}},
            "differences": {"missed_progress": dict(matched_checkpoints=7, base_groups=5,
                difference=-0.1, ci95=[-0.3, 0.2])}}}},
        clarification={"methods": {}, "paired_differences_vs_temporalrag": {}},
        repeated_calibration={"calibration_checkpoints": 20,
            "mean_absolute_score_difference": {"response_decision": 0.5},
            "error_label_agreement": {"missed_progress": dict(denominator=40, observed=1, kappa=None)}},
        method_costs={"methods": {}},
        telemetry={key: None for key in (
            "http_attempts", "http_200", "attempts_with_duration", "attempts_missing_duration",
            "mean_http_attempt_duration_seconds", "responses_with_usage", "retrievals_with_timing",
            "retrievals_missing_timing", "mean_initial_retrieval_seconds")},
        cohort_revision=dict(version="cohort-completion-v1", replaced_trajectories=22,
                             replacement_session_scope=80),
    )
    result["telemetry"]["provider_reported_usage_totals"] = {}
    audit = dict(complete=True, issues=[], dataset=data, artifact_counts={"session_reviews": 480},
                 evaluation_checkpoint_counts=dict(development=12, calibration=20, test=60))
    human = dict(paired_packets=200, assigned_judgments=400, locked_judgments=0)
    return result, audit, human


def test_report_keeps_missingness_signed_effects_denominators_and_human_status():
    result, audit, human = fixture()
    original = copy.deepcopy(result)
    report = render_final_report(result, audit, protocol_sha256="abc", human_statistics=human)
    assert "| evolution | missed_progress | 3 | 7 | 0.4286 |" in report
    assert "-0.1000" in report and "区间包含0，方向尚不确定" in report
    assert "| missed_progress | 40 | 1 | 未测得 |" in report
    assert "当前实际锁定提交0份" in report and "选择偏差" in report
    assert result == original


def test_report_rejects_partial_or_mismatched_delivery():
    result, audit, human = fixture()
    audit["complete"] = False
    with pytest.raises(ValueError, match="successful full"):
        render_final_report(result, audit, protocol_sha256="abc", human_statistics=human)
    audit["complete"] = True
    result["task_a"]["evolution"]["checkpoints"] = 59
    with pytest.raises(ValueError, match="incomplete Task A"):
        render_final_report(result, audit, protocol_sha256="abc", human_statistics=human)
