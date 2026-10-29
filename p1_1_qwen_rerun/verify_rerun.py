"""Cross-run provenance and model-identity checks."""
import json
from pathlib import Path
import p1

ROOT = p1.ROOT
ORIGINAL = ROOT.parent / "p0_understanding_in_action"
issues = []
same_input_files = 0
for folder in ("dialogues", "checkpoints/public", "checkpoints/annotation_drafts", "inputs"):
    for source in (ORIGINAL / folder).rglob("*.json"):
        mirror = p1.P0 / source.relative_to(ORIGINAL)
        if not mirror.exists() or p1.file_digest(source) != p1.file_digest(mirror):
            issues.append("Source mismatch: " + str(source.relative_to(ORIGINAL)))
        same_input_files += 1
for source in (ORIGINAL / "retrieval").glob("P*_CP*.json"):
    mirror = p1.P0 / source.relative_to(ORIGINAL)
    if not mirror.exists() or p1.file_digest(source) != p1.file_digest(mirror):
        issues.append("Retrieval mismatch: " + source.name)
    same_input_files += 1

answer_files = list((p1.P0 / "answers").rglob("*.json"))
if len(answer_files) != 192:
    issues.append("Answer count: " + str(len(answer_files)))
for file in answer_files:
    row = p1.read(file)
    if row.get("model") != p1.ANSWER_MODEL or row.get("status") != "completed":
        issues.append("Wrong/incomplete answer: " + str(file.relative_to(ROOT)))

for path in (ROOT / "validated_annotations").glob("*.json"):
    if p1.read(path).get("review_model") != p1.JUDGE_MODEL:
        issues.append("Wrong annotation judge: " + path.name)
for path in (ROOT / "reviews").rglob("*.json"):
    if p1.read(path).get("review_model") != p1.JUDGE_MODEL:
        issues.append("Wrong first judge: " + str(path.relative_to(ROOT)))

logs = []
for path in (ROOT / "source_p0/logs/calls.jsonl", ROOT / "logs/calls.jsonl"):
    logs.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
usage = {}
for row in logs:
    if row.get("status") != "success":
        continue
    model = row.get("model", "unknown")
    item = usage.setdefault(model, {"successful_api_calls_including_retries": 0,
                                    "input_tokens": 0, "output_tokens": 0})
    item["successful_api_calls_including_retries"] += 1
    item["input_tokens"] += (row.get("usage") or {}).get("input_tokens", 0) or 0
    item["output_tokens"] += (row.get("usage") or {}).get("output_tokens", 0) or 0
if set(usage) != {p1.ANSWER_MODEL, p1.JUDGE_MODEL}:
    issues.append("Unexpected model in success logs: " + repr(sorted(usage)))

result = {"verified_at": p1.now(), "identical_original_input_files": same_input_files,
          "answer_files": len(answer_files), "models": {"answers": p1.ANSWER_MODEL,
          "judge": p1.JUDGE_MODEL}, "usage_observed": usage, "issues": issues,
          "complete": not issues and p1.read(ROOT / "audit.json").get("complete") is True}
p1.write(ROOT / "verification.json", result)
print(json.dumps({"complete": result["complete"], "identical_original_input_files": same_input_files,
                  "answers": len(answer_files), "issues": issues[:8]}, ensure_ascii=False), flush=True)
if not result["complete"]:
    raise SystemExit(1)
