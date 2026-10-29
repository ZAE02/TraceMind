"""Record a surface candidate gate and preserve all interrupted/rejected drafts."""
import shutil
import subprocess
import generator as g
import run_longitudinal as runner

root = g.ROOT
lock = root / "generation_logs/runner.lock"
if lock.exists():
    pid = g.read_json(lock)["pid"]
    check = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Process -Id %d -ErrorAction SilentlyContinue" % pid], capture_output=True)
    if check.returncode == 0 and check.stdout.strip():
        raise g.GenerationError("Generator still alive")
    g.write_json(root / "generation_logs/interrupted_attempts/v5_stop_lock.json", g.read_json(lock))
    lock.unlink()
backup = root / "configs/revisions/unsent-candidate-expression-gate-v5"
if backup.exists():
    raise g.GenerationError("Immutable configuration snapshot already exists")
backup.mkdir(parents=True)
for name in ("generation_config.json", "client_system_prompt.txt", "counselor_system_prompt.txt"):
    shutil.copyfile(str(root / "configs" / name), str(backup / name))
config = g.read_json(root / "configs/generation_config.json")
config.update(prompt_version="pilot-v5-unsent-candidate-expression-gate", expression_gate=True,
              expression_gate_max_revisions=2, closing_policy="explicit_client_goodbye_and_brief_acknowledgement")
g.write_json(root / "configs/generation_config.json", config)
archived = []
for case_id in g.CASE_IDS:
    for path in sorted((root / "dialogues" / case_id).glob("S*.json")):
        data = g.read_json(path)
        if data["status"] != "reviewed_by_model":
            number = data["session_number"]
            runner.archive_revision(case_id, number, data.get("revision", 0))
            archived.append({"case_id": case_id, "session": number, "saved_utterances": len(data["utterances"]),
                             "inflight_outcome": "unknown if interrupted", "reason": "candidate expression gate targeted intervention"})
            path.unlink()
g.write_json(root / "generation_logs/interrupted_attempts/v5_expression_intervention.json", {"recorded_at": g.now(), "archived": archived})
metadata = g.read_json(root / "generation_logs/generation_metadata.json")
for run in metadata["runs"]:
    if run.get("phase") == "full_longitudinal" and run.get("status") == "running":
        run.update(status="stopped_for_documented_revision", finished_at=g.now(), note="v5 expression intervention; all prior records retained")
g.write_json(root / "generation_logs/generation_metadata.json", metadata)
revisions = g.read_json(root / "audit/configuration_revisions.json")
revisions["revisions"].append({"timestamp": g.now(), "version": config["prompt_version"],
    "reason": "Reject unsent counselor candidates with quotation/word-rating/closing-exploration phrasing and regenerate one utterance before client sees it; at most two candidates. Earlier drafts preserved.",
    "clinical_judgment_automated": False, "psychological_gold_added": False,
    "original_configs": "configs/revisions/unsent-candidate-expression-gate-v5", "archived": archived})
g.write_json(root / "audit/configuration_revisions.json", revisions)
print("Preserved %d drafts and enabled v5 unsent-candidate checks" % len(archived))
