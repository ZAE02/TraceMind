"""Export reviewed, frozen public inputs separately from researcher annotations."""
from pathlib import Path
import generator as g


def evidence_ids(value):
    result = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "evidence":
                if not isinstance(item, list):
                    raise g.GenerationError("Evidence must be an ID list")
                result.extend(item)
            elif key == "utterance_id":
                result.append(item)
            else:
                result.extend(evidence_ids(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(evidence_ids(item))
    return result


def freeze(root, case_id, proposal):
    number = proposal["session_number"]
    checkpoint = proposal["checkpoint_id"]
    ranges = {"CP1": (2, 3), "CP2": (5, 6), "CP3": (8,)}
    if number not in ranges[checkpoint]:
        raise g.GenerationError("Checkpoint outside its predeclared stage")
    previous = g.public_history(root, case_id, number)
    data = g.read_json(root / "dialogues" / case_id / ("S%02d.json" % number))
    g.validate_model_review(root, case_id, number)
    rows = g.visible_utterances(data["utterances"])
    indices = [i for i, row in enumerate(rows) if row["id"] == proposal["freeze_utterance_id"]]
    if len(indices) != 1 or rows[indices[0]]["role"] != "client":
        raise g.GenerationError("Freeze must identify exactly one real client utterance")
    index = indices[0]
    prefix, latest = rows[:index], rows[index]
    public = {"schema_version": "1.0", "synthetic": True, "case_id": case_id,
              "checkpoint_id": checkpoint, "current_session": number,
              "session_background": {"current_relative_week": number, "typical_interval_days": 7,
                                     "format": "同一来访者与咨询师的连续个人咨询"},
              "previous_session_dialogues": previous, "current_session_dialogue": prefix,
              "current_user_utterance": latest}
    valid = {row["id"] for session in previous for row in session["utterances"]}
    valid.update(row["id"] for row in rows[:index+1])
    annotation = proposal["annotation"]
    required = ("supported_understanding", "uncertainties", "relevant_evidence",
                "appropriate_response_goals", "unsupported_presuppositions")
    if any(not isinstance(annotation.get(key), list) or not annotation[key] for key in required):
        raise g.GenerationError("Incomplete checkpoint annotation")
    cited = evidence_ids(annotation)
    if not cited or any(not isinstance(item, str) or item not in valid for item in cited):
        raise g.GenerationError("Checkpoint cites unavailable/future evidence")
    frozen_hash = g.digest(public)
    public_path = root / "checkpoints/public" / (case_id + "_" + checkpoint + ".json")
    if public_path.exists() and g.digest(g.read_json(public_path)) != frozen_hash:
        raise g.GenerationError("Previously frozen input changed; preserve it and its answers as an explicit new version")
    g.write_json(public_path, public)
    # These facts are for future interactive simulation only. They never enter a test request.
    plan = g.read_json(root / "configs/case_plans" / (case_id + ".json"))
    private = {"synthetic": True, "case_id": case_id, "checkpoint_id": checkpoint,
               "current_session": number, "client_profile": plan["client_profile"],
               "private_life_history": plan["private_life_history"],
               "occurred_life_events": [{"session_number": c["session_number"], "life_events": c["life_events"]}
                                       for c in plan["session_cards"] if c["session_number"] < number],
               "current_session_card": {k: g.card_for(plan, number)[k]
                                        for k in ("life_events", "private_experiences", "still_unspoken_details")},
               "notice": "Synthetic private simulator settings; not objective clinical truth; no future events."}
    g.write_json(root / "checkpoints/private" / (case_id + "_" + checkpoint + ".json"), private)
    record = {"synthetic": True, "case_id": case_id, "checkpoint_id": checkpoint,
              "session_number": number, "freeze_utterance_id": latest["id"],
              "public_input": "public/%s_%s.json" % (case_id, checkpoint),
              "public_input_sha256": frozen_hash, "annotation": annotation,
              "annotation_kind": proposal.get("annotation_kind", "gpt-6.1-sol/high model annotation; human review not performed"),
              "unique_gold_response": False,
              "original_next_response_reference": rows[index+1]["id"]}
    annotation_path = root / "checkpoints/annotations" / (case_id + "_" + checkpoint + ".json")
    if annotation_path.exists():
        old = g.read_json(annotation_path)
        if g.digest(old) != g.digest(record):
            archive = root / "revisions/checkpoints" / (case_id + "_" + checkpoint) / ("annotation_" + g.digest(old)[:12] + ".json")
            if not archive.exists():
                g.write_json(archive, old)
    g.write_json(annotation_path, record)
    md = ["# %s %s · S%02d" % (case_id, checkpoint, number), "",
          "> 拟真合成材料；模型标注；未进行人工审阅。没有唯一标准回复。", "",
          "冻结发言：**%s**" % latest["id"], "", latest["text"], ""]
    titles = {"supported_understanding": "有依据的理解", "uncertainties": "尚不确定",
              "relevant_evidence": "可见证据", "appropriate_response_goals": "合理探索目标",
              "unsupported_presuppositions": "应避免的无依据预设",
              "history_needed": "需要利用的历史", "difficulty_notes": "任务难度与限制"}
    import json
    for key, title in titles.items():
        md += ["## " + title, ""]
        for item in annotation.get(key, []):
            md += ["- " + (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False))]
        md.append("")
    (root / "checkpoints" / (case_id + "_" + checkpoint + ".md")).write_text("\n".join(md), encoding="utf-8")
    return record


def export_case(case_id):
    summary = g.read_json(g.ROOT / "audit/agent_reviews" / case_id / "case_summary.json")
    proposals = summary["proposed_checkpoints"]
    if {p["checkpoint_id"] for p in proposals} != {"CP1", "CP2", "CP3"} or len(proposals) != 3:
        raise g.GenerationError("Each case needs exactly three distinct checkpoints")
    identity = summary.get('reviewer_model', 'gpt-6.1-sol') + '/' + summary.get('reasoning_effort', 'high')
    records = [freeze(g.ROOT, case_id, dict(p, annotation_kind=p.get('annotation_kind',
                    identity + ' model annotation; human review not performed'))) for p in proposals]
    g.write_json(g.ROOT / "checkpoints" / (case_id + ".json"), {
        "synthetic": True, "case_id": case_id, "status": "model_annotated", "checkpoints": records})
    plan = g.read_json(g.ROOT / "configs/case_plans" / (case_id + ".json"))
    review_input = {"synthetic": True, "case_id": case_id, "human_review_performed": False,
        "notice": "Independent second reviewer input: no first-review scores, researcher labels or psychological development plan.",
        "stable_profile": plan["client_profile"], "established_biography": plan["private_life_history"],
        "planned_factual_events": [{"session_number": card["session_number"], "relative_week": card["relative_week"],
                                    "life_events": card["life_events"], "unspoken_detail_reference": card["still_unspoken_details"]}
                                   for card in plan["session_cards"]],
        "actual_sessions": []}
    for number in range(1, 9):
        data = g.read_json(g.ROOT / "dialogues" / case_id / ("S%02d.json" % number))
        rows = g.visible_utterances(data["utterances"])
        review_input["actual_sessions"].append({"session_number": number, "relative_week": number,
                                              "utterances": rows, "transcript_sha256": g.digest(rows)})
    g.write_json(g.ROOT / "audit/second_review_inputs" / (case_id + ".json"), review_input)
    return records


def main():
    counts = []
    for case_id in g.CASE_IDS:
        counts.extend(export_case(case_id))
    g.write_json(g.ROOT / "audit/checkpoint_checks.json", {
        "checked_at": g.now(), "count": len(counts), "passed": len(counts) == 9,
        "checks": ["real client freeze", "public prefix only", "no future evidence IDs",
                   "private and annotations stored separately", "no unique gold reply"],
        "public_input_hashes": {r["case_id"] + "_" + r["checkpoint_id"]: r["public_input_sha256"] for r in counts}})
    print("Exported %d frozen checkpoints" % len(counts))


if __name__ == "__main__":
    main()
