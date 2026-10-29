"""Neutral within-session world-state guard and higher counselor reasoning after documented errors."""
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
    g.write_json(root / "generation_logs/interrupted_attempts/v8_stop_lock.json", g.read_json(lock))
    lock.unlink()
backup = root / "configs/revisions/world-state-and-role-reasoning-v8"
if backup.exists():
    raise g.GenerationError("Immutable snapshot exists")
backup.mkdir(parents=True)
for name in ("generation_config.json", "client_system_prompt.txt", "counselor_system_prompt.txt"):
    shutil.copyfile(str(root / "configs" / name), str(backup / name))
config = g.read_json(root / "configs/generation_config.json")
config.update(prompt_version="pilot-v8-world-state-and-role-reasoning", role_reasoning_effort={"client": "low", "counselor": "medium"},
    client_process_instruction="只输出本轮来访者1–4句实际发言。这是同一场连续谈话，角色没有离开会话去执行工作。"
    "你刚说尚未做、打算做的事情，在本场仍未做；被问结果时说明未做，不为配合问话编造已经运行、已验证或新结果。"
    "保持当前卡片人物和事件事实，可不同意咨询师。回顾此前会话的生活事件时，优先用之前那次、上次聊过的，"
    "不要直接把旧对话的这周、上周当成当前周；会话通常相隔一周。不要给出角色说明。")
config["counselor_process_instruction"] += "来访者修正了你的解释，后续应采用修正，不再把旧说法作为事实。不确定仍可以用已经公开的具体例子继续探索，不能反复以一次定不了结束讨论。先了解来访者此时想谈什么，不预设他一定苦恼或已经完全改变。"
config["candidate_editing_instruction"] += "保留来访者最新修正和事件状态；尚未做的事情不写成已做。不确定仍可以依据具体历史继续探索，不预设必须有困扰。"
g.write_json(root / "configs/generation_config.json", config)
prompts = {role: (root / "configs" / (role + "_system_prompt.txt")).read_text(encoding="utf-8") for role in g.ROLES}
entries = []
for case_id in g.CASE_IDS:
    for path in sorted((root / "dialogues" / case_id).glob("S*.json")):
        data = g.read_json(path)
        number = data["session_number"]
        if data["status"] in g.COMPLETE_STATUSES and not (case_id == "C03" and number == 8):
            continue
        keep = min(3, len(data["utterances"])) if case_id == "C03" and number == 8 else len(data["utterances"])
        prefix = runner.archive_revision(case_id, number, data.get("revision", 0), keep_prefix=keep)
        entries.append({"case_id": case_id, "session": number, "preserved_prefix_length": keep,
                        "saved_before_revision": len(data["utterances"]), "inflight_outcome": "unknown if interrupted"})
        if not prefix:
            path.unlink()
            continue
        active = dict(config, generation_revision="%s-S%02d-v8-prefix" % (case_id, number))
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
g.write_json(root / "generation_logs/interrupted_attempts/v8_intervention.json", {"recorded_at": g.now(), "entries": entries})
metadata = g.read_json(root / "generation_logs/generation_metadata.json")
for run in metadata["runs"]:
    if run.get("phase") == "full_longitudinal" and run.get("status") == "running":
        run.update(status="stopped_for_documented_revision", finished_at=g.now(), note="World-state/time guard; counselor reasoning medium; prefixes preserved")
g.write_json(root / "generation_logs/generation_metadata.json", metadata)
revisions = g.read_json(root / "audit/configuration_revisions.json")
revisions["revisions"].append({"timestamp": g.now(), "version": config["prompt_version"],
    "reason": "Repeated within-session planned-to-completed state error and shallow history integration; generic client world-state guard and counselor-only medium reasoning.",
    "api_documentation": "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses",
    "default_reasoning_effort": "low", "role_overrides": config["role_reasoning_effort"],
    "original_configs": "configs/revisions/world-state-and-role-reasoning-v8", "psychological_gold_added": False, "entries": entries})
g.write_json(root / "audit/configuration_revisions.json", revisions)
print("v8 applied; client low/counselor medium, current world-state frozen")
