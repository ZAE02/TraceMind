"""Blind scoring of C4/C5 formulations and C4/C5/C5-minus/direct responses."""
import argparse
import concurrent.futures
import hashlib
import json
import os
import random
import sys

import p1
import mechanisms

ROOT = p1.ROOT


def candidates(name):
    arms = [("C4", "A"), ("C5", "A"), ("C4", "B"), ("C5", "B"), ("C5_minus", "B")]
    if name in mechanisms.DIRECT_NODES:
        arms.append(("direct", "B"))
    labels = list("MNOPQR")[:len(arms)]
    random.Random("P1-mechanism-blind-" + name).shuffle(labels)
    return {labels[i]: arms[i] for i in range(len(arms))}


def prepare():
    mapping = {}
    for name in p1.NODES:
        mapping[name] = {label: list(arm) for label, arm in candidates(name).items()}
        packet = {"checkpoint": name,
                  "public_input": p1.read(p1.FROZEN / "checkpoints/public" / (name + ".json")),
                  "candidate_answers": {label: p1.read(ROOT / "mechanisms/answers" / name /
                                                       (arm[0] + "_" + arm[1] + ".json"))["raw_text"]
                                        for label, arm in candidates(name).items()}}
        p1.write(ROOT / "mechanisms/blind_packets" / (name + ".json"), packet)
    key_path = ROOT / "mechanisms/internal_blind_key.json"
    if key_path.exists() and p1.read(key_path) != mapping:
        raise RuntimeError("Mechanism blind key changed")
    p1.write(key_path, mapping)


def judge(secret, item):
    name, label, task = item
    path = ROOT / "mechanisms/reviews" / name / (label + "_" + task + ".json")
    if path.exists():
        return
    mapping = p1.read(ROOT / "mechanisms/internal_blind_key.json")
    condition, expected_task = mapping[name][label]
    if task != expected_task:
        raise RuntimeError("Task mismatch")
    packet = p1.read(ROOT / "mechanisms/blind_packets" / (name + ".json"))
    public = packet["public_input"]
    reference = p1.read(ROOT / "validated_annotations" / (name + ".json"))["validated_reference"]
    if task == "B":
        actual_input = p1.read(ROOT / "mechanisms/response_inputs" / name / (condition + ".json"))
    else:
        actual_input = {"previous_sessions": public["previous_sessions"],
                        "record_created_before_current_session": True}
    content = {"public_history": public,
               "reference": {k: reference[k] for k in
                             ("established", "unresolved", "useful_exploration", "unsupported_assumptions")},
               "candidate_id": label, "candidate_visible_input": actual_input,
               "candidate_answer": packet["candidate_answers"][label], "task": task}
    system = p1.JUDGE_A if task == "A" else p1.JUDGE_B
    for retry in range(3):
        raw, fingerprint, usage, latency = p1.api(secret, p1.JUDGE_MODEL, system,
                                                  dict(content, retry_instruction="" if not retry else
                                                       "仅引用本次公开历史中真实存在的发言ID；按指定字段返回JSON。"),
                                                  "mechanism_review:" + name + ":" + label + ":" + task +
                                                  (":retry%d" % retry if retry else ""), 2200)
        try:
            parsed = p1.parse_json(raw)
            fields = (("evidence_grounding", "progress_understanding", "uncertainty_awareness") if task == "A" else
                      ("history_utilization", "exploration_value", "response_appropriateness"))
            if any(not isinstance(parsed["scores"][k], int) or not 0 <= parsed["scores"][k] <= 4 for k in fields):
                raise ValueError("Invalid score")
            if task == "B":
                for err in ("unsupported_presupposition", "redundant_exploration", "missed_progress"):
                    item = parsed["errors"][err]
                    if not isinstance(item["occurred"], bool):
                        raise ValueError("Invalid error")
                    if item["occurred"] and (not item.get("trigger_quote") or not item.get("evidence_ids")):
                        raise ValueError("Missing error citation")
                    if not set(item.get("evidence_ids", [])) <= p1.visible_ids(public):
                        raise ValueError("Future/invalid error citation")
            break
        except (ValueError, TypeError, KeyError) as exc:
            p1.write(ROOT / "mechanisms/invalid_reviews" / name / (label + "_" + task + "_%d.json" % retry),
                     {"raw_text": raw, "reason": str(exc), "request_sha256": fingerprint})
    else:
        raise RuntimeError("Could not score mechanism " + name + label + task)
    p1.write(path, {"checkpoint": name, "candidate_id": label, "task": task,
                    "blind": True, "assessment": parsed, "raw_text": raw, "request_sha256": fingerprint,
                    "usage": usage, "latency_seconds": latency, "model": p1.JUDGE_MODEL})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "review", "all"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.phase in ("prepare", "all"):
        prepare()
    if args.phase in ("review", "all"):
        secret = os.environ.get("DASHSCOPE_API_KEY") or sys.stdin.readline().strip()
        if not secret:
            raise RuntimeError("Missing DASHSCOPE_API_KEY")
        mapping = p1.read(ROOT / "mechanisms/internal_blind_key.json")
        jobs = [(name, label, arm[1]) for name, labels in mapping.items() for label, arm in labels.items()]
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for future in concurrent.futures.as_completed([pool.submit(judge, secret, job) for job in jobs]):
                future.result()
        print("Mechanism reviews", len(jobs), "completed", flush=True)


if __name__ == "__main__":
    main()
