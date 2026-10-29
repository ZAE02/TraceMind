"""Retain real prefixes, edit unsent candidates, and avoid early closure padding."""
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
    g.write_json(root / "generation_logs/interrupted_attempts/v6_stop_lock.json", g.read_json(lock))
    lock.unlink()
backup = root / "configs/revisions/editing-context-and-session-clock-v6"
if backup.exists():
    raise g.GenerationError("Immutable snapshot exists")
backup.mkdir(parents=True)
for name in ("generation_config.json", "client_system_prompt.txt", "counselor_system_prompt.txt"):
    shutil.copyfile(str(root / "configs" / name), str(backup / name))
config = g.read_json(root / "configs/generation_config.json")
config.update(prompt_version="pilot-v6-unsent-editor-and-session-clock", candidate_editing_instruction=
    "你为一条尚未发送的咨询师候选发言作口语编辑。把回应改成直接、具体的1–3句，保留公开事实和不确定性。"
    "发言不得含中文或英文双引号，不评价词句，不延伸抽象比喻。把提问落在实际事情、选择和当时想法上。"
    "如果候选作了无依据断言，应改为询问或保留不确定；不得增加新的心理结论或情绪原因。"
    "遵循会话时间提示，前半段继续自然探索，不提前告别；不以注意到就够、不用解释打断来访者。"
    "只输出编辑后的实际咨询发言。")
g.write_json(root / "configs/generation_config.json", config)
prompts = {role: (root / "configs" / (role + "_system_prompt.txt")).read_text(encoding="utf-8") for role in g.ROLES}
preserved = {("C01", 3): 5, ("C03", 5): 6}
entries = []
for case_id in g.CASE_IDS:
    for path in sorted((root / "dialogues" / case_id).glob("S*.json")):
        data = g.read_json(path)
        if data["status"] == "reviewed_by_model":
            continue
        number = data["session_number"]
        count = min(preserved.get((case_id, number), 0), len(data["utterances"]))
        prefix = runner.archive_revision(case_id, number, data.get("revision", 0), keep_prefix=count)
        entries.append({"case_id": case_id, "session": number, "saved_before_revision": len(data["utterances"]),
                        "preserved_prefix_length": count, "inflight_outcome": "unknown if interrupted"})
        if not prefix:
            path.unlink()
            continue
        active = dict(config, generation_revision="%s-S%02d-v6-prefix" % (case_id, number))
        plan = g.read_json(root / "configs/case_plans" / (case_id + ".json"))
        previous = g.public_history(root, case_id, number)
        ledger = g.read_json(root / "facts" / (case_id + ".json"))
        fingerprint = g.digest({"generation": active, "case": plan, "prompts": prompts, "previous": previous,
                               "confirmed_additions": [f for f in ledger.get("confirmed_additions", []) if f["first_session"] < number]})
        seed = {"schema_version": "1.0", "synthetic": True, "case_id": case_id, "session_number": number,
                "relative_week": number, "status": "generating", "human_review_status": "not_performed",
                "continuity_review": {"status": "pending", "issues": [], "reviewer": None}, "created_at": g.now(),
                "config_hash": fingerprint, "prompt_version": config["prompt_version"], "generation_config_snapshot": active,
                "revision": data.get("revision", 0)+1, "preserved_prefix_length": count, "utterances": prefix}
        g.write_json(path, seed)
        g.render_dialogue(root, seed)
g.write_json(root / "generation_logs/interrupted_attempts/v6_intervention.json", {"recorded_at": g.now(), "entries": entries})
metadata = g.read_json(root / "generation_logs/generation_metadata.json")
for run in metadata["runs"]:
    if run.get("phase") == "full_longitudinal" and run.get("status") == "running":
        run.update(status="stopped_for_documented_revision", finished_at=g.now(), note="v6 intervention: candidate editing role and early clock; real prefixes preserved")
g.write_json(root / "generation_logs/generation_metadata.json", metadata)
revisions = g.read_json(root / "audit/configuration_revisions.json")
revisions["revisions"].append({"timestamp": g.now(), "version": config["prompt_version"],
    "reason": "Separate oral editing instruction for unsent counselor candidates; early neutral time notice; reject stage directions; preserve real substantive prefixes.",
    "original_configs": "configs/revisions/editing-context-and-session-clock-v6", "psychological_gold_added": False, "entries": entries})
g.write_json(root / "audit/configuration_revisions.json", revisions)
print("v6 applied; real prefixes preserved, no text manually changed")
