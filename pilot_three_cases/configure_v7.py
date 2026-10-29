"""Apply reviewer-specified generic counseling process guidance, with immutable provenance."""
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
    g.write_json(root / "generation_logs/interrupted_attempts/v7_stop_lock.json", g.read_json(lock))
    lock.unlink()
backup = root / "configs/revisions/counseling-process-focus-v7"
if backup.exists():
    raise g.GenerationError("Immutable snapshot exists")
backup.mkdir(parents=True)
for name in ("generation_config.json", "client_system_prompt.txt", "counselor_system_prompt.txt"):
    shutil.copyfile(str(root / "configs" / name), str(backup / name))
guidance = ("请现在只生成这一轮实际咨询师回应。一个动作只澄清到足以理解当时经历即可；"
    "来访者说记不清，或同类动作细节已连续问两轮时，应回应其实际困扰与感受，再用一个开放问题探索在意的意义、生活影响、关系或选择。"
    "不要逐项盘问目光、桌面、光标或微小动作先后。来访者主动提到的工作和生活处境值得回应。"
    "不把不确定回忆总结为确定原因，不把很少、偶尔、这次写成从来、总是、所有情况。"
    "避免用非此即彼的问题替来访者限定原因，允许两种体验共存；不要预设新的心理解释。"
    "用直接自然的1–3句话，不引用或评价词句。")
config = g.read_json(root / "configs/generation_config.json")
config.update(prompt_version="pilot-v7-counseling-process-focus", counselor_process_instruction=guidance)
config["candidate_editing_instruction"] += guidance
g.write_json(root / "configs/generation_config.json", config)
prompts = {role: (root / "configs" / (role + "_system_prompt.txt")).read_text(encoding="utf-8") for role in g.ROLES}
entries = []
for case_id in g.CASE_IDS:
    for path in sorted((root / "dialogues" / case_id).glob("S*.json")):
        data = g.read_json(path)
        number = data["session_number"]
        if data["status"] in g.COMPLETE_STATUSES and not (case_id == "C01" and number == 3):
            continue
        keep = 0 if case_id == "C01" and number == 3 else len(data["utterances"])
        prefix = runner.archive_revision(case_id, number, data.get("revision", 0), keep_prefix=keep)
        entries.append({"case_id": case_id, "session": number, "preserved_prefix_length": keep,
                        "saved_before_revision": len(data["utterances"]), "inflight_outcome": "unknown if interrupted"})
        if not prefix:
            path.unlink()
            continue
        active = dict(config, generation_revision="%s-S%02d-v7-prefix" % (case_id, number))
        plan = g.read_json(root / "configs/case_plans" / (case_id + ".json"))
        previous = g.public_history(root, case_id, number)
        ledger = g.read_json(root / "facts" / (case_id + ".json"))
        fingerprint = g.digest({"generation": active, "case": plan, "prompts": prompts, "previous": previous,
                               "confirmed_additions": [f for f in ledger.get("confirmed_additions", []) if f["first_session"] < number]})
        seed = {"schema_version": "1.0", "synthetic": True, "case_id": case_id, "session_number": number,
                "relative_week": number, "status": "generating", "human_review_status": "not_performed",
                "continuity_review": {"status": "pending", "issues": [], "reviewer": None}, "created_at": g.now(),
                "config_hash": fingerprint, "prompt_version": config["prompt_version"], "generation_config_snapshot": active,
                "revision": data.get("revision", 0)+1, "preserved_prefix_length": keep, "utterances": prefix}
        g.write_json(path, seed)
        g.render_dialogue(root, seed)
g.write_json(root / "generation_logs/interrupted_attempts/v7_intervention.json", {"recorded_at": g.now(), "entries": entries})
metadata = g.read_json(root / "generation_logs/generation_metadata.json")
for run in metadata["runs"]:
    if run.get("phase") == "full_longitudinal" and run.get("status") == "running":
        run.update(status="stopped_for_documented_revision", finished_at=g.now(), note="Generic counseling process focus; other real prefixes preserved")
g.write_json(root / "generation_logs/generation_metadata.json", metadata)
revisions = g.read_json(root / "audit/configuration_revisions.json")
revisions["revisions"].append({"timestamp": g.now(), "version": config["prompt_version"],
    "reason": "Requested model reviewer identified repeated action-detail interrogation and categorical overstatement; add generic professional guidance to all cases.",
    "original_configs": "configs/revisions/counseling-process-focus-v7", "psychological_gold_added": False, "entries": entries})
g.write_json(root / "audit/configuration_revisions.json", revisions)
print("v7 generic process focus applied; other actual prefixes preserved")
