"""Completion and leakage audit for all P1 deliverables."""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import p1
import mechanisms

ROOT = p1.ROOT
P0 = p1.P0


def count(pattern):
    return len(list(ROOT.glob(pattern)))


def run():
    issues = []
    manifest = p1.read(ROOT / "frozen_manifest.json")
    for relative, expected in manifest["files"].items():
        frozen = p1.FROZEN / relative
        source = P0 / relative
        if not frozen.exists() or p1.file_digest(frozen) != expected:
            issues.append("frozen file changed: " + relative)
        if not source.exists() or p1.file_digest(source) != expected:
            issues.append("P0 source changed: " + relative)
    counts = {
        "frozen_files": len(manifest["files"]),
        "validated_annotations": count("validated_annotations/*.json"),
        "first_reviews": count("reviews/*/*.json"),
        "second_reviews": count("second_reviews/*/*.json"),
        "C4_formulations": count("mechanisms/C4_formulations/*.json"),
        "C5_snapshots": count("mechanisms/C5_snapshots/*.json"),
        "mechanism_answers": count("mechanisms/answers/*/*.json"),
        "mechanism_reviews": count("mechanisms/reviews/*/*.json"),
        "budget_memories": count("budget_match/C5_memories/*.json"),
        "budget_answers": count("budget_match/answers/*.json"),
        "budget_reviews": count("budget_match/reviews/*/*.json"),
        "C4_full_formulations": count("full_access/formulations/*.json"),
        "C4_full_answers": count("full_access/answers/*.json"),
        "full_access_reviews": count("full_access/reviews/*/*.json"),
    }
    expected_second = len(p1.read(ROOT / "second_review_targets.json")["targets"]) if (ROOT / "second_review_targets.json").exists() else 0
    expected_counts = {"validated_annotations": 24, "first_reviews": 192, "second_reviews": expected_second,
                       "C4_formulations": 24, "C5_snapshots": 48, "mechanism_answers": 126,
                       "mechanism_reviews": 126, "budget_memories": 24, "budget_answers": 24,
                       "budget_reviews": 48, "C4_full_formulations": 24,
                       "C4_full_answers": 24, "full_access_reviews": 96}
    for key, expected in expected_counts.items():
        if counts[key] != expected:
            issues.append("count %s: %d != %d" % (key, counts[key], expected))
    for name in p1.NODES:
        public = p1.read(p1.FROZEN / "checkpoints/public" / (name + ".json"))
        visible = p1.visible_ids(public)
        ann = p1.read(ROOT / "validated_annotations" / (name + ".json"))["validated_reference"]
        for field in ("established", "unresolved", "useful_exploration", "unsupported_assumptions"):
            for row in ann[field]:
                if not set(row["evidence_ids"]) <= visible:
                    issues.append("annotation leaks evidence: " + name)
        for condition in ("C4", "C5", "C5_minus"):
            context = p1.read(ROOT / "mechanisms/response_inputs" / name / (condition + ".json"))
            if p1.digest(context["previous_sessions"]) != p1.digest(public["previous_sessions"]):
                issues.append("mechanism history mismatch: " + name + condition)
            if p1.digest(context["current_dialogue"]) != p1.digest(public["current_dialogue"]):
                issues.append("mechanism current mismatch: " + name + condition)
        c5 = p1.read(ROOT / "mechanisms/response_inputs" / name / "C5.json")["formulation"]
        minus = p1.read(ROOT / "mechanisms/response_inputs" / name / "C5_minus.json")["formulation"]
        if minus.get("unresolved_questions") != [] or any(c5[k] != minus[k] for k in
                                                         ("established_understanding", "evolving_patterns", "supporting_evidence_ids")):
            issues.append("C5-minus changes more than unresolved questions: " + name)
        for condition in ("C4", "C5"):
            memory = p1.read(ROOT / "mechanisms/response_inputs" / name / (condition + ".json"))["formulation"]
            if len(json.dumps(memory, ensure_ascii=False)) > mechanisms.MEMORY_LIMIT:
                issues.append("main memory over cap: " + name + condition)
        budget = p1.read(ROOT / "budget_match/C5_memories" / (name + ".json"))
        if budget["characters"] > 1200:
            issues.append("budget memory over cap: " + name)
        all_history = p1.read(ROOT / "full_access/inputs" / (name + ".json"))["previous_sessions"]
        if p1.digest(all_history) != p1.digest(public["previous_sessions"]):
            issues.append("full access response history mismatch: " + name)
    # Verify positive observable-error quotes are verbatim in their candidate response.
    for family, key_file, review_pattern, answer_fn in (
        ("P0", ROOT / "internal/condition_key.json", "reviews/*/*_B.json",
         lambda name, condition: p1.read(p1.FROZEN / "answers" / name / (condition + "_B.json"))["raw_text"]),
        ("mechanism", ROOT / "mechanisms/internal_blind_key.json", "mechanisms/reviews/*/*_B.json",
         lambda name, condition: p1.read(ROOT / "mechanisms/answers" / name / (condition + "_B.json"))["raw_text"]),
    ):
        key = p1.read(key_file)
        for path in ROOT.glob(review_pattern):
            row = p1.read(path)
            name, label = path.parent.name, path.stem[0]
            condition = key[name][label]
            if isinstance(condition, list):
                condition = condition[0]
            answer = answer_fn(name, condition)
            for error_name, error_record in row["assessment"].get("errors", {}).items():
                if error_name != "missed_progress" and error_record.get("occurred") and error_record.get("trigger_quote") not in answer:
                    issues.append("nonverbatim error quote: " + family + name + label + error_name)
    for path in (ROOT / "analysis").glob("figure*.svg"):
        try:
            ET.parse(path)
        except ET.ParseError:
            issues.append("invalid SVG: " + path.name)
    if count("analysis/figure*.svg") != 4:
        issues.append("missing figures")
    secret_pattern = re.compile(r"sk-ws-[A-Za-z0-9._-]{50,}")
    secret_files = []
    for path in ROOT.rglob("*"):
        if path.is_file() and path.suffix in (".py", ".json", ".jsonl", ".md", ".csv", ".txt"):
            try:
                if secret_pattern.search(path.read_text(encoding="utf-8")):
                    secret_files.append(str(path.relative_to(ROOT)))
            except UnicodeError:
                pass
    if secret_files:
        issues.append("possible secret in files: " + ", ".join(secret_files))
    result = {"generated_at": p1.now(), "counts": counts, "expected_counts": expected_counts,
              "human_review_performed": False, "models": {"answers": p1.ANSWER_MODEL, "judge": p1.JUDGE_MODEL},
              "issues": sorted(set(issues)), "complete": not issues}
    p1.write(ROOT / "audit.json", result)
    print(json.dumps({"complete": result["complete"], "counts": counts, "issues": result["issues"][:12]},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    run()
