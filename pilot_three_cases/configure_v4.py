"""Targeted prompt simplification after recurrent expression failures; archive every draft."""
import shutil
import generator as g
import run_longitudinal as runner

root = g.ROOT
lock = root / "generation_logs/runner.lock"
if lock.exists():
    import subprocess
    pid = g.read_json(lock)["pid"]
    result = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Process -Id %d -ErrorAction SilentlyContinue" % pid], capture_output=True)
    if result.returncode == 0 and result.stdout.strip():
        raise g.GenerationError("Generator still alive")
    g.write_json(root / "generation_logs/interrupted_attempts/v4_stop_lock.json", g.read_json(lock))
    lock.unlink()
backup = root / "configs/revisions/concise-role-prompts-v4"
if backup.exists():
    raise g.GenerationError("Do not overwrite a prompt revision snapshot")
backup.mkdir(parents=True)
for name in ("generation_config.json", "client_system_prompt.txt", "counselor_system_prompt.txt"):
    shutil.copyfile(str(root / "configs" / name), str(backup / name))
prompt = """你是一名咨询师，正在与同一来访者进行持续多次的个人心理咨询。
每次只说当前这一轮实际会说的1–3句话，自然口语，直接回应具体处境、感受或选择。
每轮选择一个主要探索方向，提问宜简短；也可以只反映或回应，不必每轮提问。
使用自己的话。发言中不使用任何中文或英文双引号，不评价来访者词句写得准确、真实或重要。
把具体事情和关系作为探索的落点。少作抽象比喻解析；来访者说不清时允许停留，而非继续拆同一个词。
历史用于理解当前处境：有必要时回顾一次具体证据，之后继续回应新内容，不重复同一对比。
心理解释保持暂定，尊重来访者修正。过去的理解不是永久事实；新行为的原因仍可不同。
你只知道实际公开对话和本次会话基本信息，不知道私人设定、未来事件或研究答案。
避免无依据诊断、强行洞察、说教和列表建议。允许困惑、没有进展及未解决的结束。
临近结束时简短总结或确认未完事项；来访者告别时只告别一次。
只输出实际咨询发言，不输出内部分析、角色标签或任何说明。"""
(root / "configs/counselor_system_prompt.txt").write_text(prompt + "\n", encoding="utf-8")
client = root / "configs/client_system_prompt.txt"
client.write_text(client.read_text(encoding="utf-8") + "\n说话时优先讲具体事情、自己做过什么和当时想法。通常只需1–4句；不必每轮创造比喻或延伸咨询师的比喻。可以简单回答、犹豫或不同意，保持人物原有说话习惯。\n", encoding="utf-8")
config = g.read_json(root / "configs/generation_config.json")
config["prompt_version"] = "pilot-v4-concise-concrete-role-prompts"
g.write_json(root / "configs/generation_config.json", config)
interrupted = []
for case_id in g.CASE_IDS:
    for path in sorted((root / "dialogues" / case_id).glob("S*.json")):
        data = g.read_json(path)
        if data["status"] not in g.COMPLETE_STATUSES:
            number = data["session_number"]
            runner.archive_revision(case_id, number, data.get("revision", 0))
            interrupted.append({"case_id": case_id, "session": number, "saved_utterances": len(data["utterances"]),
                                "inflight_remote_outcome": "unknown", "reason": "generic style repair after rejected S2 drafts"})
            path.unlink()  # Already preserved in the immutable archive.
g.write_json(root / "generation_logs/interrupted_attempts/v4_style_intervention.json", {
    "recorded_at": g.now(), "interrupted": interrupted, "secret_saved": False})
metadata = g.read_json(root / "generation_logs/generation_metadata.json")
for run in metadata["runs"]:
    if run.get("phase") == "full_longitudinal" and run.get("status") == "running":
        run.update(status="stopped_for_documented_revision", finished_at=g.now(),
                   note="See retained revisions and v4_style_intervention.json; no unsuccessful review was overridden.")
g.write_json(root / "generation_logs/generation_metadata.json", metadata)
revisions = g.read_json(root / "audit/configuration_revisions.json")
revisions["revisions"].append({"timestamp": g.now(), "version": config["prompt_version"],
    "reason": "Repeated style failures despite appended constraints; replace counselor prompt with concise concrete role rules and add client style reminder.",
    "original_configs": "configs/revisions/concise-role-prompts-v4", "psychological_answers_added": False,
    "discarded_partial_drafts": interrupted})
g.write_json(root / "audit/configuration_revisions.json", revisions)
print("Versioned v4 generic role prompts; preserved %d interrupted drafts" % len(interrupted))
