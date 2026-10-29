"""Small independent model answers, with no annotations or private simulator facts."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
import generator as g

PROMPT = """依据截至当前的实际咨询对话，给出当前心理理解与下一轮咨询回应。
区分已有证据和仍需确认的推断，利用相关历史提出具体且合理的探索。
不要把心理解释当成已核实事实，不作诊断。输出一个JSON对象：
understanding（简短心理理解）、uncertainties（仍不确定的事项列表）、
next_response（咨询师实际会说的1–3句）、evidence_ids（引用的实际发言编号列表）。
这是拟真合成咨询任务。没有唯一标准回应。"""


def answer(model, path, config, secret):
    public = g.read_json(path)
    name = path.stem
    output = g.ROOT / "audit/smoke_answers" / model / (name + ".json")
    fingerprint = g.digest(public)
    if output.exists():
        saved = g.read_json(output)
        if saved.get("public_input_sha256") == fingerprint and saved.get("status") == "completed":
            return saved
        raise g.GenerationError("Preserve existing smoke answer before changing input")
    active = dict(config, model=model, prompt_version="checkpoint-smoke-v1", role_reasoning_effort={}, call_log_filename="smoke_calls.jsonl")
    if model == "deepseek-v4-flash":
        active.update(reasoning_effort="high", max_output_tokens=8192)
    payload = g.build_payload("counselor", PROMPT, active, public)
    if set(public) != {"schema_version", "synthetic", "case_id", "checkpoint_id", "current_session", "session_background",
                       "previous_session_dialogues", "current_session_dialogue", "current_user_utterance"}:
        raise g.GenerationError("Unexpected fields in public test input")
    text, call_id, response = g.call_api(g.ROOT, active, payload, {
        "purpose": "checkpoint_smoke", "case_id": public["case_id"],
        "checkpoint_id": public["checkpoint_id"], "prompt_version": active["prompt_version"],
        "public_input_sha256": fingerprint}, secret)
    try:
        normalized = text.strip()
        if normalized.startswith("```json"):
            normalized = normalized[7:].rsplit("```", 1)[0]
        elif normalized.startswith("```"):
            normalized = normalized[3:].rsplit("```", 1)[0]
        parsed = json.loads(normalized)
    except ValueError:
        parsed = None
    result = {"synthetic": True, "status": "completed", "model_requested": model,
              "model_returned": response.get("model"), "reasoning_effort": active["reasoning_effort"],
              "max_output_tokens": active["max_output_tokens"], "public_input_sha256": fingerprint,
              "call_id": call_id, "created_at": g.now(), "raw_text": text,
              "call_log_reference": "generation_logs/smoke_calls.jsonl",
              "parsed_answer": parsed, "format_valid": isinstance(parsed, dict) and
              all(key in parsed for key in ("understanding", "uncertainties", "next_response", "evidence_ids")),
              "annotation_visible": False, "private_facts_visible": False,
              "evaluation_scope": "one answer per model per node; feasibility only, not a model ranking or RAG comparison"}
    g.write_json(output, result)
    print(model + " " + name + " saved", flush=True)
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=g.CASE_IDS)
    args = parser.parse_args()
    config = g.read_json(g.ROOT / "configs/generation_config.json")
    secret = os.environ.get(config["api_key_env"], "")
    if not secret:
        raise g.GenerationError("Missing credential environment")
    paths = sorted((g.ROOT / "checkpoints/public").glob((args.case + "_*.json") if args.case else "*.json"))
    if len(paths) != (3 if args.case else 9):
        raise g.GenerationError("Three case inputs or nine complete exported inputs required")
    run_path = g.ROOT / "audit" / ("smoke_run_%s.json" % args.case if args.case else "smoke_run.json")
    result = {"started_at": g.now(), "scope": "limited independent answers", "models": {}}
    for model in ("qwen3.8-27b", "deepseek-v4-flash"):
        probe = g.ROOT / "audit/deepseek_connection_probe.json"
        if model == "deepseek-v4-flash" and probe.exists() and g.read_json(probe).get("status") == "failed":
            result["models"][model] = {"status": "unavailable", "error": g.read_json(probe)["error"],
                                      "saved_answers": 0, "probe_reference": "audit/deepseek_connection_probe.json"}
            g.write_json(run_path, result)
            continue
        try:
            # First request verifies the model/format before issuing the remaining calls.
            first = answer(model, paths[0], config, secret)
            with ThreadPoolExecutor(max_workers=3) as executor:
                remaining = list(executor.map(lambda path: answer(model, path, config, secret), paths[1:]))
            rows = [first] + remaining
            result["models"][model] = {"status": "completed", "count": len(rows),
                                      "format_valid_count": sum(row["format_valid"] for row in rows)}
        except g.GenerationError as exc:
            result["models"][model] = {"status": "stopped", "error": g.scrub(str(exc), secret),
                                      "saved_answers": len(list((g.ROOT / "audit/smoke_answers" / model).glob("*.json")))}
        g.write_json(run_path, result)
    result["finished_at"] = g.now()
    g.write_json(run_path, result)


if __name__ == "__main__":
    main()
