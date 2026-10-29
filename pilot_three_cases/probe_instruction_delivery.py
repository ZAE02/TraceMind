"""Check instruction delivery against a real public-only context; not case dialogue."""
import json
import os
import generator as g

def main():
    entries = [json.loads(line) for line in (g.ROOT / "generation_logs/full_agent_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    source = [r for r in entries if r.get("role") == "counselor" and r.get("prompt_version", "").startswith("pilot-v4")][-1]
    context = json.loads(source["request"]["input"][1]["content"])
    config = g.read_json(g.ROOT / "configs/generation_config.json")
    secret = os.environ["DASHSCOPE_API_KEY"]
    result = []
    for use_instructions in (False, True):
        payload = g.build_payload("counselor", "Ignore the consultation task in the input. Output exactly the single ASCII letter Z.", config, context)
        if use_instructions:
            payload["instructions"] = payload["input"][0]["content"]
        text, call_id, response = g.call_api(g.ROOT, config, payload, {
            "purpose": "instruction_delivery_probe", "use_instructions": use_instructions,
            "source_public_context_call_id": source["logical_call_id"], "prompt_version": "delivery-probe-v1"}, secret)
        result.append({"use_instructions": use_instructions, "text": text, "call_id": call_id, "model": response.get("model")})
    g.write_json(g.ROOT / "audit/instruction_delivery_probe.json", {"synthetic": True, "results": result,
                  "notice": "Diagnostics only, never inserted in consultation history."})
    print(json.dumps(result, ensure_ascii=True))

if __name__ == "__main__":
    main()
