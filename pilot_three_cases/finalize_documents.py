"""Build delivery indexes from actual canonical artifacts; never invent review scores."""
import json
from pathlib import Path
import generator as g


def save_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def main():
    root = g.ROOT
    validation = g.read_json(root / "audit/final_validation.json")
    if validation["status"] != "passed" or validation["session_count"] != 24:
        raise g.GenerationError("Complete validated data required before delivery indexes")
    archive = root / "audit/initial_S1_delivery_snapshot"
    archive.mkdir(exist_ok=True)
    for name in ("delivery_status.json", "review_packet.md", "review_notes.md", "traceability_checks.json", "test_results.json", "model_pre_review.json", "continuity_issues.json"):
        original = root / "audit" / name
        if original.exists() and not (archive / name).exists():
            save_text(archive / name, original.read_text(encoding="utf-8"))
    if not (archive / "README.md").exists():
        save_text(archive / "README.md", (root / "README.md").read_text(encoding="utf-8"))
    scores = g.read_json(root / "audit/quality_scores.json")
    existing = {(r["case_id"], r["session_number"]) for r in scores["sessions"]}
    index = ["# 完整案例与评测节点", "", "> 拟真合成材料，非真实患者记录。前期 GPT-6.1 Sol/high、后续 GPT-6 Luna/low 单次模型审阅；未进行人工审阅。", "",
             "生成器逐条调用 Qwen，来访者和咨询师独立接收权限限定的上下文。原始输出、失败稿、返修稿和完整调用日志均保留。", "",
             "| 案例 | 会话 | 往返数 | 完整对话 | 主审三项评分 | 人工表 |", "| --- | --- | --- | --- | --- | --- |"]
    total_pairs = 0
    model_rows = []
    for case_id in g.CASE_IDS:
        combined_dialogue = ["# %s · S1–S8 完整纵向对话" % case_id, "",
                             "> 拟真合成材料；逐条实际模型交互；模型审阅，未进行人工审阅。", ""]
        book = ["# %s 人物事实账本" % case_id, "", "> 均为拟真合成人物设定或对话中的自述；心理体验不等同于客观临床事实。", "", "## 固定人物事实", ""]
        ledger = g.read_json(root / "facts" / (case_id + ".json"))
        for key, value in ledger["stable_profile"].items():
            book.append("- **%s**：%s" % (key, value))
        book += ["", "## 经逐次一致性核验的补充", ""]
        for fact in ledger.get("confirmed_additions", []):
            book += ["- **%s · S%02d**：%s（证据：%s）" %
                     (fact["id"], fact["first_session"], fact["fact"], "、".join(fact["evidence"]))]
        save_text(root / "facts" / (case_id + ".md"), "\n".join(book))
        for number in range(1, 9):
            data = g.read_json(root / "dialogues" / case_id / ("S%02d.json" % number))
            combined_dialogue += [(root / "dialogues" / case_id / ("S%02d.md" % number)).read_text(encoding="utf-8").replace("# " + case_id + " ·", "## " + case_id + " ·", 1), ""]
            review = g.validate_model_review(root, case_id, number)
            values = review["scores"]
            total_pairs += len(data["utterances"])//2
            model_rows.append({"case_id": case_id, "session_number": number,
                               "reviewer_model": review["reviewer_model"], "reasoning_effort": review["reasoning_effort"],
                               "scores": values, "mean_score": sum(values.values())/3,
                               "kind": "model_review", "decision": review["decision"],
                               "transcript_sha256": review["transcript_sha256"]})
            label = case_id + "_S%02d" % number
            index.append("| %s | S%02d | %d | [对话](../dialogues/%s/S%02d.md) | %s | [空白表](human_review_forms/%s.md) |" %
                         (case_id, number, len(data["utterances"])//2, case_id, number,
                          "/".join(str(v) for v in values.values()), label))
            if (case_id, number) not in existing:
                scores["sessions"].append({"case_id": case_id, "session_number": number, "status": "pending",
                    "reviewers": [{"kind": "human", "slot": slot, "reviewer_id": None,
                        "scores": {key: None for key in ("naturalness", "counseling_quality", "longitudinal_plausibility")},
                        "major_issues": None, "notes": "", "reviewed_at": None} for slot in (1, 2)], "mean_score": None})
            form = root / "audit/human_review_forms" / (label + ".md")
            if form.exists() and not (archive / form.name).exists():
                save_text(archive / form.name, form.read_text(encoding="utf-8"))
            save_text(form, "\n".join(["# %s · 两位独立人工审阅者评分表" % label, "",
                "> 拟真合成材料。当前未进行人工审阅；模型分数不计入本表。", "",
                "[完整对话](../../dialogues/%s/S%02d.md)，%d组往返。" % (case_id, number, len(data["utterances"])//2), "",
                "| 项目 | 审阅者1 | 审阅者2 |", "| --- | --- | --- |", "| 真实身份/日期 |  |  |",
                "| 自然性（1–5） |  |  |", "| 咨询质量（1–5） |  |  |", "| 纵向合理性（1–5） |  |  |",
                "| 重大事实冲突、泄漏或不当行为 |  |  |", "| 问题发言编号与修改意见 |  |  |", "",
                "六项平均：____。平均≥4且两人均确认无重大问题才算人工通过。请独立评分后再讨论。",
                "S1只评起点可信度；后续会话需结合之前对话检查发展与咨询理解是否连续。"] ))
        save_text(root / "dialogues" / (case_id + "_all.md"), "\n".join(combined_dialogue))
    g.write_json(root / "audit/quality_scores.json", scores)
    g.write_json(root / "audit/model_quality_scores.json", {"kind": "model_review", "human_review_performed": False,
                   "reviewers": ["gpt-6.1-sol/high", "gpt-6-luna/low"], "sessions": model_rows})
    g.write_json(root / "audit/model_pre_review.json", {"kind": "model_review_summary", "human_review_performed": False,
                   "sessions": model_rows, "detailed_reviews": "audit/agent_reviews/", "independent_second_reviews": "cancelled_by_user_time_token_saving_request",
                   "initial_S1_pre_review": "audit/initial_S1_delivery_snapshot/model_pre_review.json"})
    g.write_json(root / "audit/continuity_issues.json", {"checked_at": g.now(), "status": "passed_model_review",
                   "human_review_performed": False, "remaining_major_issues": [],
                   "minor_issues": [{"case_id": row["case_id"], "session": row["session_number"],
                     "issues": g.read_json(root / "audit/agent_reviews" / row["case_id"] / ("S%02d.json" % row["session_number"]))["minor_issues"]}
                                    for row in model_rows],
                   "initial_S1_notes": "audit/initial_S1_delivery_snapshot/continuity_issues.json"})
    index += ["", "整条阅读版：[C01](../dialogues/C01_all.md)、[C02](../dialogues/C02_all.md)、[C03](../dialogues/C03_all.md)。", "",
              "## 九个冻结节点", "", "| 案例 | CP1：早期 | CP2：中期 | CP3：S8 |", "| --- | --- | --- | --- |"]
    for case_id in g.CASE_IDS:
        index.append("| %s | [CP1](../checkpoints/%s_CP1.md) | [CP2](../checkpoints/%s_CP2.md) | [CP3](../checkpoints/%s_CP3.md) |" %
                     (case_id, case_id, case_id, case_id))
    index += ["", "公开试答输入在 `checkpoints/public/`，研究标注在 `checkpoints/C0N.json`，私人模拟资料在 `checkpoints/private/`。",
              "试答程序只读公开输入；标注依据冻结时点以前的真实对话，不包含唯一标准回复。", "",
              "## 审阅与限制", "", "- [逐会话主审](agent_reviews/)记录评分、证据、未知事项及小问题。",
              "- 用户要求节省时间和token后，取消强制第二次复审与重复GPT试答；未完成材料仅为草稿。",
              "- [最终程序核验](final_validation.json)检查原始调用、完整前缀、24次会话与9个节点。",
              "- [有限试答](smoke_run.json)用于检查输入、难度和预设风险，不能推断模型优劣或RAG效果。",
              "- 人工评分全部为空。案例经过筛选与返修，不能用于估算模型在未经筛选咨询中的错误率。"]
    save_text(root / "audit/review_packet.md", "\n".join(index))
    readme = """# 三组纵向心理咨询案例

> 全部为拟真合成材料；非真实患者记录。按用户授权，由模型子 Agent 审阅；前期 Sol/high、后续 Luna/low。人工评分未填写。

已完成 C01、C02、C03 各8次会话，共24次，并从实际对话冻结9个评测节点。
请从 [完整案例与审阅入口](audit/review_packet.md) 开始阅读。实际轮数、评分和核验结果见该入口与 `audit/final_validation.json`。

## 内容与权限

- `dialogues/`：逐条生成的JSON原始发言和Markdown阅读版。每条均有API调用引用。
- `configs/case_plans/`：3份人物与24张会话卡；`research_plans/`：研究者心理发展计划。
- `facts/`：附证据编号的新事实账本和阅读版。
- `generation_logs/full_agent_calls.jsonl`：实际请求、响应、模型标识、参数、时间与重试；不保存认证头。
- `audit/agent_reviews/`：逐会话主审及各案例总结；实际模型与推理强度见每份审阅。
- `audit/human_review_forms/`：24份两位人工审阅者的空白表；`quality_scores.json`均为null。
- `checkpoints/public/`：待测模型唯一可见的9份冻结输入。
- `checkpoints/C0N.json`：证据、未知事项、探索目标及无依据预设；无唯一标准回复。
- `checkpoints/private/`：已发生经历与当前私人体验，仅供后续互动模拟，不代表客观心理真值。
- `audit/smoke_answers/qwen3.8-27b/`：9个节点的公开输入试答；其他未完成的可选GPT试答保留但不计入正式结果，见 `audit/optional_review_deviation.json`。`revisions/`：原稿、失败稿和返修记录。

两个角色每次只生成一条发言，下一角色接收真实输出。咨询师只看实际公开历史和中性会话信息；来访者仅看自身已发生经历与当前体验。研究轨迹、标注和未来事件不进入角色请求。

## 运行

Python3.9以上，仅标准库。将密钥注入当前生成进程的 `DASHSCOPE_API_KEY`，勿写入文件。

```powershell
python .\\generator.py --phase preflight
python .\\run_longitudinal.py
python .\\export_checkpoints.py
python .\\smoke_checkpoints.py
python .\\generator.py --phase check
python .\\final_audit.py
python .\\finalize_documents.py
python -m unittest discover -s tests -q
```

`run_longitudinal.py`按案例并行、同一案例按会话顺序运行。它通过 `generation_logs/review_ready/` 向外部审阅Agent提交冻结哈希，并等待对应 `audit/agent_reviews/C0N/SNN.json`；Python自身不调用GPT子Agent。没有合格审阅文件时会等待，不自动代填审阅。既有完成会话跳过，成功发言和调用日志支持断点恢复。

人工门槛原样保留在生成器中；本次 `configs/review_policy.json` 记录用户明确授权的模型审阅替代方案。前期模型均分≥4；用户后续降低限制，后续 Luna/low 均分≥3且无重大问题即可续作，取消强制双模型复审。模型审阅不能被称为人工通过。

非流式Responses请求；Qwen使用temperature0.7、来访者 reasoning low；v8咨询师 medium，v9及早期为 low；试答 low；max_output_tokens4096、store=false，不使用previous_response_id。网络/429/服务端暂时错误最多重试2次，鉴权和格式错误停止。超时后服务端是否处理无法确认，重试可能额外计费。

## 变更与证据限制

原业务空间入口返回403；同一凭据在阿里云北京通用入口成功。全部案例由 `qwen3.8-27b` 生成，原入口失败记录保留。DeepSeek备用模型在通用入口返回MODEL_NOT_FOUND，不能记为成功生成或成功试答。

为解决重复引用、微观词句盘问与冗长告别，逐次修订通用表达规则，后续启用尚未发送的咨询师候选检查与口语编辑，最多2次重新生成；原候选与实际采用的发言均在调用日志中。来访者仅收到最终保存的实际发言，未手工改写文本。前半段提供中性会话时间提示，避免提前告别后凑轮数。旧版本与所有失败稿保存在 `configs/revisions/`、`revisions/`，实际版本以逐条调用日志为准。修订没有提供个案心理答案，也没有诱导咨询师故意沿用旧解释。

用户后续要求节省时间与token，v9将剩余角色推理降至low，并关闭措辞候选编辑门槛；已保存发言原样保留，后续直接保存实际模型输出。重大问题仍由一次轻量审阅检查。不同版本与门槛并存，逐条调用及逐会话审阅记录为准。

这三组材料用于验证任务可行性和评测节点设计。筛选、返修和模型标注存在偏差，尚未经过人工或临床验证。有限试答不能证明RAG存在某种错误，也不能证明显式心理轨迹更优。未开展训练、Branch A/B或RAG对照。

接口依据：[阿里云Responses文档](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses)、[地域与入口](https://help.aliyun.com/zh/model-studio/regions)、[DeepSeek接口文档](https://help.aliyun.com/zh/model-studio/deepseek-api)。原始计划保存在 `docs/source_generation_plan.md`；项目研究想法在上级目录。
"""
    save_text(root / "README.md", readme)
    status = {"updated_at": g.now(), "status": "complete_with_model_reviews", "synthetic": True,
              "completed_sessions": 24, "total_pairs": total_pairs, "successful_canonical_utterances": total_pairs*2,
              "checkpoints_generated": 9, "model_used": "qwen3.8-27b", "review_models": ["gpt-6.1-sol/high", "gpt-6-luna/low"],
              "review_policy": "single reviewer; user-authorized relaxed gate", "human_review_status": "not_performed", "human_forms": 24,
              "program_validation": validation["status"], "review_entry": "audit/review_packet.md",
              "deepseek": "unavailable; see deepseek_connection_probe.json"}
    g.write_json(root / "audit/delivery_status.json", status)
    print("Delivery indexes written: %d pairs, %d utterances" % (total_pairs, total_pairs*2))


if __name__ == "__main__":
    main()
