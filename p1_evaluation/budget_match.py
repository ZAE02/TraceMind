"""Response-context budget sensitivity: C4 and compressed C5 under 1,200 characters."""
import argparse
import concurrent.futures
import json
import os
import random
import sys

import p1
import mechanisms

ROOT = p1.ROOT
CAP = 1200
PROMPT = ("把给出的同一来访者心理理解记录压缩成不超过1200个JSON字符。"
          "只输出有效JSON对象，保持 established_understanding、evolving_patterns、"
          "unresolved_questions、supporting_evidence_ids 四个数组；保留最重要的已知、变化和未解问题。"
          "不得增加新事实或证据ID，不把推测升格成确定结论。")


def compress(secret, name):
    path = ROOT / "budget_match/C5_memories" / (name + ".json")
    public = p1.read(p1.FROZEN / "checkpoints/public" / (name + ".json"))
    source = p1.read(ROOT / "mechanisms/C5_snapshots" /
                     ("%s_S%02d.json" % (public["case_id"], public["session"] - 1)))["memory"]
    allowed = mechanisms.ids_from_sessions(public["previous_sessions"])
    value = {field: list(source[field]) for field in mechanisms.FIELDS}
    removed = {field: 0 for field in mechanisms.FIELDS}
    def length():
        return len(json.dumps(value, ensure_ascii=False))
    # Preserve unresolved issues as long as possible; trim tail entries with a fixed order.
    while length() > CAP:
        field = next((field for field in ("established_understanding", "evolving_patterns",
                                        "supporting_evidence_ids", "unresolved_questions")
                      if len(value[field]) > 1), None)
        if field is None:
            field = max(mechanisms.FIELDS[:3], key=lambda key: len(value[key][0]) if value[key] else 0)
            if not value[field]:
                raise RuntimeError("Could not fit memory into shared cap")
            value[field][0] = value[field][0][:-20].rstrip() if len(value[field][0]) > 20 else ""
            removed[field] += 1
        else:
            value[field].pop()
            removed[field] += 1
    mechanisms.normalize_memory(value, allowed)
    p1.write(path, {"memory": value, "source_sha256": p1.digest(source),
                    "method": "deterministic_tail_trim_v1", "characters": length(),
                    "cap": CAP, "removed_or_shortened": removed})


def answer(secret, name):
    path = ROOT / "budget_match/answers" / (name + ".json")
    if path.exists():
        return
    public = p1.read(p1.FROZEN / "checkpoints/public" / (name + ".json"))
    memory = p1.read(ROOT / "budget_match/C5_memories" / (name + ".json"))["memory"]
    context = mechanisms.response_context(public, memory)
    p1.write(ROOT / "budget_match/inputs" / (name + ".json"), context)
    raw, fingerprint, usage, latency = p1.api(secret, p1.ANSWER_MODEL, mechanisms.RESPONSE_PROMPT,
                                              context, "budget_response:" + name, 700)
    p1.write(path, {"checkpoint": name, "condition": "C5_budget", "raw_text": raw,
                    "input_sha256": p1.digest(context), "memory_sha256": p1.digest(memory),
                    "request_sha256": fingerprint, "usage": usage, "latency_seconds": latency})


def prepare():
    mapping = {}
    for name in p1.NODES:
        labels = list("ST")
        random.Random("P1-budget-blind-" + name).shuffle(labels)
        mapping[name] = {labels[0]: "C4_budget", labels[1]: "C5_budget"}
        c4 = p1.read(ROOT / "mechanisms/answers" / name / "C4_B.json")
        c5 = p1.read(ROOT / "budget_match/answers" / (name + ".json"))
        packet = {"public_input": p1.read(p1.FROZEN / "checkpoints/public" / (name + ".json")),
                  "candidates": {labels[0]: c4["raw_text"], labels[1]: c5["raw_text"]}}
        p1.write(ROOT / "budget_match/blind_packets" / (name + ".json"), packet)
    key = ROOT / "budget_match/internal_blind_key.json"
    if key.exists() and p1.read(key) != mapping:
        raise RuntimeError("Budget blind key changed")
    p1.write(key, mapping)


def review(secret, item):
    name, label = item
    path = ROOT / "budget_match/reviews" / name / (label + ".json")
    if path.exists():
        return
    condition = p1.read(ROOT / "budget_match/internal_blind_key.json")[name][label]
    packet = p1.read(ROOT / "budget_match/blind_packets" / (name + ".json"))
    public = packet["public_input"]
    reference = p1.read(ROOT / "validated_annotations" / (name + ".json"))["validated_reference"]
    actual_input = (p1.read(ROOT / "mechanisms/response_inputs" / name / "C4.json") if condition == "C4_budget"
                    else p1.read(ROOT / "budget_match/inputs" / (name + ".json")))
    context = {"public_history": public,
               "reference": {k: reference[k] for k in
                             ("established", "unresolved", "useful_exploration", "unsupported_assumptions")},
               "candidate_id": label, "candidate_visible_input": actual_input,
               "candidate_answer": packet["candidates"][label], "task": "B"}
    for retry in range(3):
        raw, fingerprint, usage, latency = p1.api(secret, p1.JUDGE_MODEL, p1.JUDGE_B,
                                                  dict(context, retry_instruction="" if not retry else
                                                       "请用有效JSON和本次公开历史中真实发言ID。"),
                                                  "budget_review:" + name + ":" + label + ":" + str(retry), 2200)
        try:
            parsed = p1.parse_json(raw)
            for metric in ("history_utilization", "exploration_value", "response_appropriateness"):
                if not isinstance(parsed["scores"][metric], int) or not 0 <= parsed["scores"][metric] <= 4:
                    raise ValueError("Bad score")
            for err in ("unsupported_presupposition", "redundant_exploration", "missed_progress"):
                row = parsed["errors"][err]
                if not isinstance(row["occurred"], bool):
                    raise ValueError("Bad error")
                if row["occurred"] and (not row.get("trigger_quote") or not row.get("evidence_ids")):
                    raise ValueError("Missing error evidence")
                if not set(row.get("evidence_ids", [])) <= p1.visible_ids(public):
                    raise ValueError("Invalid evidence ID")
            break
        except (KeyError, TypeError, ValueError):
            continue
    else:
        raise RuntimeError("Cannot review " + name + label)
    p1.write(path, {"checkpoint": name, "candidate_id": label, "assessment": parsed,
                    "raw_text": raw, "request_sha256": fingerprint, "usage": usage,
                    "latency_seconds": latency})


def parallel(fn, items, workers):
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for future in concurrent.futures.as_completed([pool.submit(fn, item) for item in items]):
            future.result()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("compress", "answers", "prepare", "review", "all"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.phase in ("answers", "review", "all"):
        secret = os.environ.get("DASHSCOPE_API_KEY") or sys.stdin.readline().strip()
        if not secret:
            raise RuntimeError("Missing DASHSCOPE_API_KEY")
    if args.phase in ("compress", "all"):
        parallel(lambda name: compress(None, name), p1.NODES, args.workers)
    if args.phase in ("answers", "all"):
        parallel(lambda name: answer(secret, name), p1.NODES, args.workers)
    if args.phase in ("prepare", "all"):
        prepare()
    if args.phase in ("review", "all"):
        mapping = p1.read(ROOT / "budget_match/internal_blind_key.json")
        parallel(lambda item: review(secret, item),
                 [(name, label) for name, labels in mapping.items() for label in labels], args.workers)


if __name__ == "__main__":
    main()
