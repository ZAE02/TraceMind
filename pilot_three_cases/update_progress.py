"""Report actual current files without claiming unfinished generation is complete."""
import generator as g

def main():
    root = g.ROOT
    rows, approved, complete = [], 0, 0
    for case_id in g.CASE_IDS:
        for number in range(1, 9):
            path = root / "dialogues" / case_id / ("S%02d.json" % number)
            data = g.read_json(path) if path.exists() else {}
            status = data.get("status", "not_generated")
            approved += status == "reviewed_by_model"
            complete += status in g.COMPLETE_STATUSES
            rows.append({"case_id": case_id, "session": number, "status": status,
                         "utterances": len(data.get("utterances", [])), "prompt_version": data.get("prompt_version")})
    result = {"updated_at": g.now(), "target_sessions": 24, "complete_sessions": complete,
              "approved_sessions": approved, "human_review_performed": False, "sessions": rows,
              "checkpoint_exports": len(list((root / "checkpoints/public").glob("*.json")))}
    g.write_json(root / "audit/in_progress_status.json", result)
    text = ["# 当前生成进度", "", "> 拟真合成材料；按用户授权采用GPT-6.1 Sol高强度模型审阅，人工分数为空。", "",
            "更新：%s。目标24次会话；当前完整%d次、模型主审通过%d次、已导出节点%d个。" %
            (result["updated_at"], complete, approved, result["checkpoint_exports"]), "",
            "| 案例 | 会话 | 保存发言数 | 状态 |", "| --- | --- | --- | --- |"]
    for row in rows:
        text.append("| %s | S%02d | %d | %s |" % (row["case_id"], row["session"], row["utterances"], row["status"]))
    text += ["", "状态reviewed_by_model代表指定模型主审通过；awaiting_model_review代表完整稿待审；generating/interrupted不算完成。", "",
             "完整调用日志：`generation_logs/full_agent_calls.jsonl`；原稿、失败稿及中断稿：`revisions/`；配置变更：`audit/configuration_revisions.json`。",
             "全部生成完成后再提取9个冻结节点、独立复审并试答。未开展训练或RAG对照。"]
    (root / "audit/progress.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    print("Complete %d, approved %d, checkpoints %d" % (complete, approved, result["checkpoint_exports"]))

if __name__ == "__main__":
    main()
