"""Canonical transcript provenance and frozen evaluation boundary checks."""
import json
import generator as g


def main():
    report = {"checked_at": g.now(), "synthetic": True, "issues": [], "sessions": [],
              "review_policy": g.read_json(g.ROOT / 'configs/review_policy.json'),
              "human_review_performed": False}
    records = {}
    with (g.ROOT / "generation_logs/full_agent_calls.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            if record.get("status") == "success":
                records[record["logical_call_id"]] = record
    count = 0
    for case_id in g.CASE_IDS:
        previous = []
        for number in range(1, 9):
            path = g.ROOT / "dialogues" / case_id / ("S%02d.json" % number)
            data = g.read_json(path)
            rows = data["utterances"]
            report["issues"].extend(g.validate_transcript(data))
            try:
                review = g.validate_model_review(g.ROOT, case_id, number)
            except g.GenerationError as exc:
                report["issues"].append(str(exc))
                review = {}
            if data.get("human_review_status") != "not_performed":
                report["issues"].append("Incorrect human status: " + str(path))
            for index, row in enumerate(rows):
                count += 1
                call = records.get(row["call_id"])
                if not call or call.get("spoken_text") != row["text"] or call.get("utterance_id") != row["id"]:
                    report["issues"].append("Missing exact API provenance: " + row["id"])
                    continue
                payload = call["request"]
                context = json.loads(payload["input"][1]["content"])
                if context.get("previous_session_dialogues") != previous:
                    report["issues"].append("Historical input mismatch: " + row["id"])
                if context.get("current_session_dialogue") != g.visible_utterances(rows[:index]):
                    report["issues"].append("Current prefix mismatch: " + row["id"])
                if row["role"] == "counselor" and set(context) - {"session", "previous_session_dialogues", "current_session_dialogue", "time_notice"}:
                    report["issues"].append("Private counselor input: " + row["id"])
                if payload.get("store") is not False or payload.get("stream") is not False or "previous_response_id" in payload:
                    report["issues"].append("Shared state/stream in request: " + row["id"])
            previous.append({"session_number": number, "utterances": g.visible_utterances(rows)})
            scores = review.get("scores", {})
            second_scores = {}
            combined_mean = None  # User cancelled mandatory duplicate review.
            report["sessions"].append({"case_id": case_id, "session": number, "pairs": len(rows)//2,
                "reviewer_model": review.get('reviewer_model'), "reasoning_effort": review.get('reasoning_effort'),
                "model_scores": scores, "mean_model_score": sum(scores.values())/len(scores) if scores else None,
                "second_model_scores": second_scores, "two_model_reviewer_mean": combined_mean,
                "transcript_sha256": g.digest(g.visible_utterances(rows))})
        valid = {row["id"] for session in previous for row in session["utterances"]}
        for fact in g.read_json(g.ROOT / "facts" / (case_id + ".json")).get("confirmed_additions", []):
            if not fact.get("evidence") or not set(fact["evidence"]) <= valid:
                report["issues"].append("Ledger evidence unavailable: " + fact.get("fact", ""))
    for path in sorted((g.ROOT / "checkpoints/public").glob("*.json")):
        public = g.read_json(path)
        checkpoint_record = next(row for row in g.read_json(g.ROOT / "checkpoints" / (public["case_id"] + ".json"))["checkpoints"]
                                 if row["checkpoint_id"] == public["checkpoint_id"])
        if g.digest(public) != checkpoint_record["public_input_sha256"]:
            report["issues"].append("Changed frozen checkpoint: " + str(path))
        if set(public) != {"schema_version", "synthetic", "case_id", "checkpoint_id", "current_session", "session_background",
                          "previous_session_dialogues", "current_session_dialogue", "current_user_utterance"}:
            report["issues"].append("Unexpected checkpoint input field: " + str(path))
    for session in g.read_json(g.ROOT / "audit/quality_scores.json")["sessions"]:
        for reviewer in session["reviewers"]:
            if any(value is not None for value in reviewer["scores"].values()) or reviewer["reviewer_id"] is not None:
                report["issues"].append("Human score unexpectedly filled")
    smoke_records = {}
    smoke_log = g.ROOT / 'generation_logs/smoke_calls.jsonl'
    if smoke_log.exists():
        for line in smoke_log.read_text(encoding='utf-8').splitlines():
            call = json.loads(line)
            if call.get('status') == 'success':
                smoke_records[call['logical_call_id']] = call
    report['public_only_trials'] = []
    for path in sorted((g.ROOT / 'checkpoints/public').glob('*.json')):
        public = g.read_json(path)
        answer_path = g.ROOT / 'audit/smoke_answers/qwen3.8-27b' / path.name
        if not answer_path.exists():
            report['issues'].append('Missing Qwen feasibility answer: ' + path.stem)
            continue
        answer = g.read_json(answer_path)
        call = smoke_records.get(answer.get('call_id'), {})
        payload = call.get('request', {})
        inputs = payload.get('input', [])
        delivered = json.loads(inputs[1]['content']) if len(inputs) > 1 else None
        valid_ids = {row['id'] for session in public['previous_session_dialogues'] for row in session['utterances']}
        valid_ids.update(row['id'] for row in public['current_session_dialogue'])
        valid_ids.add(public['current_user_utterance']['id'])
        parsed = answer.get('parsed_answer') or {}
        if (answer.get('public_input_sha256') != g.digest(public) or delivered != public
                or call.get('spoken_text') != answer.get('raw_text')
                or answer.get('annotation_visible') is not False or answer.get('private_facts_visible') is not False
                or payload.get('store') is not False or 'previous_response_id' in payload
                or not answer.get('format_valid') or not set(parsed.get('evidence_ids', [])) <= valid_ids):
            report['issues'].append('Invalid public-only trial/provenance: ' + path.stem)
        report['public_only_trials'].append({'node': path.stem, 'model': answer.get('model_returned'),
            'format_valid': answer.get('format_valid'), 'public_input_sha256': answer.get('public_input_sha256')})
    report.update(utterances_checked=count, session_count=len(report["sessions"]),
                  checkpoint_count=len(list((g.ROOT / "checkpoints/public").glob("*.json"))),
                  status="passed" if not report["issues"] else "failed")
    g.write_json(g.ROOT / "audit/final_validation.json", report)
    print(json.dumps({key: value for key, value in report.items() if key != "sessions"}, ensure_ascii=True, indent=2))
    if report["issues"] or report["session_count"] != 24 or report["checkpoint_count"] != 9:
        raise g.GenerationError("Final audit failed or incomplete")


if __name__ == "__main__":
    main()
