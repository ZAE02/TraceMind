"""Read-only, full-scope computational delivery audit. Never sends model calls."""

from tracemind.benchmark.builder import BenchmarkBuilder, read
from tracemind.jobs import digest

from .runner import METHODS


def audit_delivery(root, queue):
    issues = []
    counts = {}
    data = BenchmarkBuilder(root, queue).audit()
    expected = {
        "plans": 20,
        "approved_plans": 20,
        "complete_trajectories": 80,
        "complete_sessions": 480,
        "checkpoints": 240,
    }
    for key, value in expected.items():
        if data.get(key) != value:
            issues.append(f"dataset {key}: {data.get(key)}/{value}")
    issues.extend(data["issues"])
    checkpoints = [read(p) for p in sorted((root / "checkpoints").glob("*.json"))]
    groups = {
        split: {c["group_id"] for c in checkpoints if c["split"] == split}
        for split in ("development", "calibration", "test")
    }
    if {s: len(g) for s, g in groups.items()} != {
        "development": 10,
        "calibration": 5,
        "test": 5,
    } or any(
        groups[a] & groups[b]
        for a, b in (
            ("development", "calibration"),
            ("development", "test"),
            ("calibration", "test"),
        )
    ):
        issues.append("base-scene split counts/isolation")

    def job(ident, sha):
        try:
            saved = queue.get(ident)
            if (
                saved["status"] != "completed"
                or saved["result_sha256"] != sha
                or digest(saved["result"]) != sha
                or digest(saved["request"]) != ident
                or saved["request"]["model"] != "gemini-3.8-flash"
                or saved["provenance"].get("provider") != "openai-compatible"
            ):
                raise ValueError("source mismatch")
            return saved
        except (KeyError, ValueError, TypeError):
            issues.append(f"invalid completed API job: {ident}")
            return None

    def artifact(path, label):
        if not path.exists():
            issues.append(f"missing {label}: {path.name}")
            return None
        value = read(path)
        source = job(value.get("job_id"), value.get("result_sha256"))
        if source is not None and "result" in value and value["result"] != source["result"]:
            issues.append(f"changed {label}: {path.name}")
        if (
            source is not None
            and "approved" in value
            and {k: value[k] for k in ("approved", "issues")} != source["result"]
        ):
            issues.append(f"changed approval: {path.name}")
        counts[label] = counts.get(label, 0) + 1
        return value

    for path in (root / "dialogues").glob("*/S*.json"):
        session = read(path)
        review = artifact(
            root / "dialogue_reviews" / f"{session['case_id']}_S{session['session']:02}.json",
            "session_reviews",
        )
        if review and (
            not review.get("approved") or review.get("dialogue_sha256") != digest(session)
        ):
            issues.append("session review no longer approves its actual source")
    for group in {c["group_id"] for c in checkpoints}:
        for stage in (2, 4, 6):
            reviewed = artifact(
                root / "counterfactual_reviews" / f"{group}_S{stage}.json", "counterfactual_reviews"
            )
            if reviewed and not reviewed.get("approved"):
                issues.append("rejected counterfactual group")
    for cp in checkpoints:
        path = root / "annotations" / (cp["id"] + ".json")
        if not path.exists():
            issues.append(f"missing annotation: {cp['id']}")
            continue
        item = read(path)
        counts["annotations"] = counts.get("annotations", 0) + 1
        for role in ("annotation", "review"):
            source = job(item.get(role + "_job_id"), item.get(role + "_result_sha256"))
            if source and role == "annotation":
                from .annotations import FIELDS

                if {k: item.get(k) for k in (*FIELDS, "oracle_evidence_ids")} != source["result"]:
                    issues.append("changed annotation content")
            if source and role == "review" and not source["result"].get("approved"):
                issues.append("annotation source review rejected")
        public_sha = digest(
            {k: cp[k] for k in ("previous_sessions", "current_dialogue", "latest_client")}
        )
        if item.get("public_sha256") != public_sha or not item.get("oracle_approved"):
            issues.append("annotation not approved for its actual checkpoint")

    tests = [c for c in checkpoints if c["split"] == "test"]
    for cp in tests:
        base = root / "experiments"
        target_path = base / "interaction_targets" / (cp["id"] + ".json")
        if not target_path.exists() or read(target_path).get("protocol") != "target-v2":
            issues.append("missing frozen target-v2")
        reviewed = artifact(base / "target_reviews" / (cp["id"] + ".json"), "target_reviews")
        if reviewed and not reviewed["result"].get("approved"):
            issues.append("rejected clarification targets")
        for method in METHODS:
            for kind in ("candidates", "understanding", "understanding_quality"):
                artifact(base / kind / cp["id"] / (method + ".json"), kind)
            if method != "evolution":
                artifact(base / "quality" / cp["id"] / (method + ".json"), "quality")
                for side in (method, "evolution"):
                    artifact(base / "access" / cp["id"] / f"{method}--{side}.json", "access")
            if method in ("temporalrag", "on_demand", "evolution"):
                path = base / "interactions" / cp["id"] / (method + ".json")
                if not path.exists() or read(path).get("status") != "completed":
                    issues.append("missing completed two-round interaction")
                else:
                    value = read(path)
                    job(value.get("score_job_id"), value.get("score_sha256"))
                    for turn in value.get("jobs", []):
                        job(turn.get("job_id"), turn.get("result_sha256"))
                    if [t["role"] for t in value.get("transcript", [])] != [
                        "counselor",
                        "client",
                        "counselor",
                        "client",
                    ]:
                        issues.append("interaction is not two alternating rounds")
                    counts["interactions"] = counts.get("interactions", 0) + 1
                artifact(
                    base / "simulator_checks" / cp["id"] / (method + ".json"), "simulator_checks"
                )
    frozen = root / "protocol/frozen.json"
    if not frozen.exists() or len(read(frozen).get("examples", [])) != 20:
        issues.append("missing twenty independently repeated calibration checkpoints")
    graph_path = root / "project/langgraph_smoke.json"
    if not graph_path.exists():
        issues.append("missing actual API LangGraph persistence/recovery proof")
    else:
        proof = read(graph_path)
        if (
            proof.get("status") != "completed_api_langgraph"
            or proof.get("memory_revisions", 0) < 1
            or set(proof.get("jobs", {})) != {"extract", "conflict", "revise", "respond"}
            or digest(proof.get("response")) != proof.get("response_sha256")
        ):
            issues.append("incomplete actual API LangGraph proof")
        for value in proof.get("jobs", {}).values():
            job(value.get("job_id"), value.get("result_sha256"))
    return {
        "complete": not issues,
        "dataset": data,
        "artifact_counts": counts,
        "issues": issues,
        "scope": "computational; human judgments remain separate",
    }
