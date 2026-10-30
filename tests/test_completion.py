from types import SimpleNamespace

from tracemind.benchmark.builder import write
from tracemind.experiments.completion import audit_delivery


def test_missing_dataset_and_calibration_cannot_pass_delivery(tmp_path):
    report = audit_delivery(tmp_path, SimpleNamespace(get_many=lambda ids: {}))
    assert not report["complete"]
    assert "dataset complete_sessions: 0/480" in report["issues"]
    assert "dataset checkpoints: 0/240" in report["issues"]
    assert "missing twenty independently repeated calibration checkpoints" in report["issues"]


def test_test_checkpoint_requires_all_methods_and_target_review(tmp_path):
    # Use the real artifact auditor, isolating dataset validation already covered
    # by benchmark tests. A complete-looking manifest cannot replace artifacts.
    from unittest.mock import patch

    write(tmp_path / "checkpoints/cp.json", {"id": "cp", "split": "test", "group_id": "g"})
    data = {
        "plans": 20,
        "approved_plans": 20,
        "complete_trajectories": 80,
        "complete_sessions": 480,
        "checkpoints": 240,
        "issues": [],
    }
    with patch("tracemind.experiments.completion.BenchmarkBuilder.audit", return_value=data):
        report = audit_delivery(tmp_path, SimpleNamespace())
    assert not report["complete"]
    assert sum(i.startswith("missing candidates:") for i in report["issues"]) == 6
    assert sum(i.startswith("missing understanding:") for i in report["issues"]) == 6
    assert sum(i.startswith("missing access:") for i in report["issues"]) == 10
    assert "missing frozen target-v2" in report["issues"]
    assert "missing annotation: cp" in report["issues"]


def test_all_checkpoint_annotation_reuses_completed_result_without_api(tmp_path, monkeypatch):
    from tracemind.experiments import annotations, project

    monkeypatch.setattr(project, "LOCAL", tmp_path)
    pipeline = project.ProjectPipeline(tmp_path / "data")
    pipeline.tracking = SimpleNamespace(requested=[])
    monkeypatch.setattr(pipeline, "setup", lambda queue: None)
    record = {"oracle_approved": True}
    monkeypatch.setattr(annotations, "advance_annotation", lambda *args: record)
    monkeypatch.setattr(
        project,
        "execute_job",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("completed annotation must not trigger another call")
        ),
    )
    assert pipeline.annotate_checkpoint(None, None, {"id": "cp"}) == record


def test_annotation_hash_covers_public_payload_without_checkpoint_metadata(tmp_path, monkeypatch):
    from tracemind.experiments.annotations import FIELDS
    from tracemind.jobs import digest

    cp = {
        "id": "cp",
        "split": "development",
        "group_id": "g",
        "previous_sessions": [],
        "current_dialogue": [],
        "latest_client": {"text": "current"},
    }
    cp["public_sha256"] = digest(cp)
    write(tmp_path / "checkpoints/cp.json", cp)
    jobs = {}

    def source(purpose, result):
        request = {"model": "gemini-3.8-flash", "purpose": purpose}
        ident = digest(request)
        jobs[ident] = {
            "request": request,
            "status": "completed",
            "result": result,
            "result_sha256": digest(result),
            "provenance": {"provider": "openai-compatible"},
        }
        return ident, digest(result)

    annotation = {**{key: [] for key in FIELDS}, "oracle_evidence_ids": []}
    aid, ahash = source("annotation", annotation.copy())
    rid, rhash = source("review", {"approved": True, "issues": []})
    annotation.update(
        public_sha256=digest(
            {k: cp[k] for k in ("previous_sessions", "current_dialogue", "latest_client")}
        ),
        oracle_approved=True,
        annotation_job_id=aid,
        annotation_result_sha256=ahash,
        review_job_id=rid,
        review_result_sha256=rhash,
    )
    write(tmp_path / "annotations/cp.json", annotation)
    monkeypatch.setattr(
        "tracemind.experiments.completion.BenchmarkBuilder.audit", lambda self: {"issues": []}
    )
    report = audit_delivery(tmp_path, SimpleNamespace(get=jobs.__getitem__))
    assert report["artifact_counts"]["annotations"] == 1
    assert not any("annotation" in i for i in report["issues"])
