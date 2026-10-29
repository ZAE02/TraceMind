"""Versioned expression-only refinement after the independent S1 rejection."""
import shutil
import generator as g

root = g.ROOT
backup = root / "configs/revisions/strong-natural-language-v3"
backup.mkdir(parents=True, exist_ok=True)
for name in ("generation_config.json", "counselor_system_prompt.txt"):
    shutil.copyfile(str(root / "configs" / name), str(backup / name))
path = root / "configs/counselor_system_prompt.txt"
path.write_text(path.read_text(encoding="utf-8") + "\n本轮表达约束：请使用1–3句完整、自然的对话，不要用引号照抄来访者词语作为回应开头，也不要给措辞评分（例如很准、很重要、挺真实、说明问题）。这些格式会显得像文本评审。用自己的话回应具体经历；关注当时的处境、做过的选择、关系与目前的咨询需要，而非连续追问一个比喻是否自然或哪里贴切。尊重说不清和不愿展开，允许没解决；一次告别即可自然结束。不能因此预设任何隐藏心理原因。\n", encoding="utf-8")
config = g.read_json(root / "configs/generation_config.json")
config["prompt_version"] = "pilot-v3-natural-dialogue-constraints"
g.write_json(root / "configs/generation_config.json", config)
metadata = g.read_json(root / "generation_logs/generation_metadata.json")
for run in metadata["runs"]:
    if run.get("phase") == "full_longitudinal" and run.get("status") == "running":
        run.update(status="stopped_for_S1_revision", finished_at=g.now(), note="C03 S1 r1 received model review 3/3/4; original versions retained.")
g.write_json(root / "generation_logs/generation_metadata.json", metadata)
print("Saved v3 generic expression constraints and preserved preceding configuration.")
