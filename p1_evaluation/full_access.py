"""Supplemental C4-full vs C5 control with equal raw historical evidence access."""
import argparse
import concurrent.futures
import json
import os
import random
import sys

import p1
import mechanisms

ROOT = p1.ROOT
PROMPT = mechanisms.MEMORY_PROMPT + "本次按需从给出的全部既往公开会话重建；不沿用上一节点的心理理解。"


def formulation(secret, name):
    path = ROOT / "full_access/formulations" / (name + ".json")
    if path.exists():
        return
    public = p1.read(p1.FROZEN / "checkpoints/public" / (name + ".json"))
    history = public["previous_sessions"]
    available = mechanisms.ids_from_sessions(history)
    context = {"case_id": public["case_id"], "checkpoint": name,
               "all_completed_public_sessions": history}
    for retry in range(3):
        raw, fingerprint, usage, latency = p1.api(secret, p1.ANSWER_MODEL, PROMPT,
                                                  dict(context, retry_instruction="" if not retry else
                                                       "请仅引用提供的发言ID，使用四数组JSON，控制总长度。"),
                                                  "C4_full_formulation:" + name + ":" + str(retry), 5500)
        try:
            value = mechanisms.normalize_memory(p1.parse_json(raw), available)
            p1.write(path, {"checkpoint": name, "memory": value, "raw_text": raw,
                            "input_sha256": p1.digest(context), "request_sha256": fingerprint,
                            "usage": usage, "latency_seconds": latency,
                            "memory_characters": len(json.dumps(value, ensure_ascii=False))})
            return
        except (ValueError, KeyError, TypeError):
            continue
    raise RuntimeError("Could not formulate " + name)


def answer(secret, name):
    path = ROOT / "full_access/answers" / (name + ".json")
    if path.exists():
        return
    public = p1.read(p1.FROZEN / "checkpoints/public" / (name + ".json"))
    memory = p1.read(ROOT / "full_access/formulations" / (name + ".json"))["memory"]
    context = mechanisms.response_context(public, memory)
    p1.write(ROOT / "full_access/inputs" / (name + ".json"), context)
    raw, fingerprint, usage, latency = p1.api(secret, p1.ANSWER_MODEL, mechanisms.RESPONSE_PROMPT,
                                              context, "C4_full_response:" + name, 700)
    p1.write(path, {"checkpoint": name, "raw_text": raw, "request_sha256": fingerprint,
                    "input_sha256": p1.digest(context), "memory_sha256": p1.digest(memory),
                    "usage": usage, "latency_seconds": latency})


def prepare():
    mapping = {}
    for name in p1.NODES:
        arms = [("C4_full", "A"), ("C5", "A"), ("C4_full", "B"), ("C5", "B")]
        labels = list("JKLM")
        random.Random("P1-full-control-" + name).shuffle(labels)
        mapping[name] = {labels[i]: list(arms[i]) for i in range(4)}
        full = p1.read(ROOT / "full_access/formulations" / (name + ".json"))
        c5a = p1.read(ROOT / "mechanisms/answers" / name / "C5_A.json")
        fullb = p1.read(ROOT / "full_access/answers" / (name + ".json"))
        c5b = p1.read(ROOT / "mechanisms/answers" / name / "C5_B.json")
        values = {("C4_full", "A"): json.dumps(full["memory"], ensure_ascii=False),
                  ("C5", "A"): c5a["raw_text"], ("C4_full", "B"): fullb["raw_text"],
                  ("C5", "B"): c5b["raw_text"]}
        p1.write(ROOT / "full_access/blind_packets" / (name + ".json"),
                 {"public_input": p1.read(p1.FROZEN / "checkpoints/public" / (name + ".json")),
                  "candidates": {label: values[tuple(arm)] for label, arm in mapping[name].items()}})
    key = ROOT / "full_access/internal_blind_key.json"
    if key.exists() and p1.read(key) != mapping:
        raise RuntimeError("Full-control blind key changed")
    p1.write(key, mapping)


def judge(secret, item):
    name, label = item
    path = ROOT / "full_access/reviews" / name / (label + ".json")
    if path.exists():
        return
    condition, task = p1.read(ROOT / "full_access/internal_blind_key.json")[name][label]
    packet = p1.read(ROOT / "full_access/blind_packets" / (name + ".json"))
    public = packet["public_input"]
    ref = p1.read(ROOT / "validated_annotations" / (name + ".json"))["validated_reference"]
    if task == "A":
        visible_input = {"previous_sessions": public["previous_sessions"],
                         "record_created_before_current_session": True}
    elif condition == "C4_full":
        visible_input = p1.read(ROOT / "full_access/inputs" / (name + ".json"))
    else:
        visible_input = p1.read(ROOT / "mechanisms/response_inputs" / name / "C5.json")
    context = {"public_history": public,
               "reference": {k: ref[k] for k in
                             ("established", "unresolved", "useful_exploration", "unsupported_assumptions")},
               "candidate_id": label, "candidate_visible_input": visible_input,
               "candidate_answer": packet["candidates"][label], "task": task}
    system = p1.JUDGE_A if task == "A" else p1.JUDGE_B
    for retry in range(3):
        raw, fingerprint, usage, latency = p1.api(secret, p1.JUDGE_MODEL, system,
                                                  dict(context, retry_instruction="" if not retry else
                                                       "按规定JSON评分，错误须引用候选原话与公开发言ID。"),
                                                  "full_control_review:" + name + ":" + label + ":" + str(retry), 2200)
        try:
            parsed = p1.parse_json(raw)
            fields = (("evidence_grounding", "progress_understanding", "uncertainty_awareness") if task == "A" else
                      ("history_utilization", "exploration_value", "response_appropriateness"))
            if any(not isinstance(parsed["scores"][m], int) or not 0 <= parsed["scores"][m] <= 4 for m in fields):
                raise ValueError("Invalid scores")
            if task == "B":
                for err in ("unsupported_presupposition", "redundant_exploration", "missed_progress"):
                    row = parsed["errors"][err]
                    if not isinstance(row["occurred"], bool):
                        raise ValueError("Invalid error")
                    if row["occurred"] and (not row.get("trigger_quote") or not row.get("evidence_ids")):
                        raise ValueError("Missing error citation")
                    if not set(row.get("evidence_ids", [])) <= p1.visible_ids(public):
                        raise ValueError("Invalid evidence IDs")
            break
        except (KeyError, TypeError, ValueError):
            continue
    else:
        raise RuntimeError("Cannot judge full control " + name + label)
    p1.write(path, {"checkpoint": name, "candidate_id": label, "task": task,
                    "assessment": parsed, "raw_text": raw, "request_sha256": fingerprint,
                    "usage": usage, "latency_seconds": latency})


def parallel(fn, items, workers):
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for future in concurrent.futures.as_completed([pool.submit(fn, item) for item in items]):
            future.result()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("formulations", "answers", "prepare", "review", "all"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.phase in ("formulations", "answers", "review", "all"):
        secret = os.environ.get("DASHSCOPE_API_KEY") or sys.stdin.readline().strip()
        if not secret:
            raise RuntimeError("Missing DASHSCOPE_API_KEY")
    if args.phase in ("formulations", "all"):
        parallel(lambda name: formulation(secret, name), p1.NODES, args.workers)
    if args.phase in ("answers", "all"):
        parallel(lambda name: answer(secret, name), p1.NODES, args.workers)
    if args.phase in ("prepare", "all"):
        prepare()
    if args.phase in ("review", "all"):
        mapping = p1.read(ROOT / "full_access/internal_blind_key.json")
        parallel(lambda item: judge(secret, item),
                 [(name, label) for name, labels in mapping.items() for label in labels], args.workers)


if __name__ == "__main__":
    main()
