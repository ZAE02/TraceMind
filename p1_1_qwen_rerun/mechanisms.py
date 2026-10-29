"""C4 on-demand, C5 rolling formulation, C5-minus, and direct-response probe."""
import argparse
import concurrent.futures
import json
import os
import sys
from pathlib import Path

import p1

ROOT = p1.ROOT
FROZEN = p1.FROZEN
FIELDS = p1.MEMORY_FIELDS
MEMORY_LIMIT = 5000  # Same character cap for C4 and C5 at response time.
DIRECT_NODES = ("P01_CP1", "P02_CP2", "P03_CP3", "P05_CP1", "P06_CP2", "P08_CP3")

MEMORY_PROMPT = ("你为同一虚构来访者维护简短、可修正的咨询理解记录。只输出有效JSON对象，"
                 "恰好包含 established_understanding、evolving_patterns、unresolved_questions、"
                 "supporting_evidence_ids 四个数组。前三个数组元素是简洁字符串；最后一个数组只列公开发言ID。"
                 "不把咨询师猜测当作来访者确认，不做诊断；保留不确定与后来修正的范围。"
                 "只依据输入中的公开对话，不使用隐藏人物计划。总JSON控制在4000汉字以内。")
ROLLING_PROMPT = MEMORY_PROMPT + ("本次为顺序更新，只使用上一份记录和这一次新完成的会话；"
                                   "需要保留仍有关联的旧认识，也要据新证据修正或删除旧假设。")
ON_DEMAND_PROMPT = MEMORY_PROMPT + ("本次为按需重建，只使用本节点检索到的既往公开片段；"
                                     "不要假装看到未提供的完整历史。")
RESPONSE_PROMPT = ("你是同一虚构长期咨询的咨询师。仅依据当前可见咨询记录和可选的心理理解记录，"
                   "直接输出本轮对来访者说的一至三句自然回应，不写分析报告或角色名。"
                   "理解记录可能不完整或有误，必须以实际对话为准；不把未证实的原因当成事实。"
                   "可以反映、澄清、追问或适度解释，优先回应当前表达。")


def ids_from_sessions(sessions):
    return {row["id"] for session in sessions for row in session["utterances"]}


def normalize_memory(obj, available_ids):
    if not isinstance(obj, dict):
        raise ValueError("Memory is not object")
    for field in FIELDS:
        if not isinstance(obj.get(field), list):
            raise ValueError("Missing memory field " + field)
    for field in FIELDS[:3]:
        if any(not isinstance(x, str) for x in obj[field]):
            raise ValueError("Non-string memory item " + field)
    if any(not isinstance(x, str) for x in obj["supporting_evidence_ids"]):
        raise ValueError("Non-string evidence ID")
    if not set(obj["supporting_evidence_ids"]) <= available_ids:
        raise ValueError("Memory cites unavailable evidence")
    if len(json.dumps(obj, ensure_ascii=False)) > MEMORY_LIMIT:
        raise ValueError("Memory exceeds shared response budget")
    return {field: obj[field] for field in FIELDS}


def generate_memory(secret, mode, context, available_ids, purpose):
    prompt = ROLLING_PROMPT if mode == "rolling" else ON_DEMAND_PROMPT
    for retry in range(3):
        raw, fingerprint, usage, latency = p1.api(secret, p1.ANSWER_MODEL, prompt,
                                                  dict(context, repair_instruction="" if not retry else
                                                       "上一版不满足JSON、证据ID或长度约束；请更简洁，仅引用输入中ID。"),
                                                  purpose + (":retry%d" % retry if retry else ""),
                                                  5500, 0.2)
        try:
            memory = normalize_memory(p1.parse_json(raw), available_ids)
            return {"memory": memory, "raw_text": raw, "request_sha256": fingerprint,
                    "usage": usage, "latency_seconds": latency, "mode": mode,
                    "memory_characters": len(json.dumps(memory, ensure_ascii=False))}
        except (ValueError, KeyError, TypeError):
            continue
    raise RuntimeError("Could not create valid formulation: " + purpose)


def rolling_case(secret, cid):
    memory = {field: [] for field in FIELDS}
    available = set()
    for session in range(1, 7):
        name = "%s_S%02d" % (cid, session)
        path = ROOT / "mechanisms/C5_snapshots" / (name + ".json")
        dialogue = p1.read(FROZEN / "dialogues" / cid / ("S%02d.json" % session))
        public_session = {"session": session, "utterances": [{k: row[k] for k in ("id", "role", "text")}
                                                       for row in dialogue["utterances"]]}
        available |= ids_from_sessions([public_session])
        if path.exists():
            saved = p1.read(path)
            memory = saved["memory"]
            if not set(memory["supporting_evidence_ids"]) <= available:
                raise RuntimeError("Saved C5 memory leaks future IDs")
            continue
        context = {"case_id": cid, "last_completed_session": session,
                   "previous_memory": memory, "new_session": public_session}
        result = generate_memory(secret, "rolling", context, available, "C5_update:" + name)
        result.update(case_id=cid, session=session, input_sha256=p1.digest(context),
                      previous_memory_sha256=p1.digest(memory), public_session_sha256=p1.digest(public_session))
        p1.write(path, result)
        memory = result["memory"]
    print(cid, "C5 snapshots complete", flush=True)


def c4_memory(secret, name):
    path = ROOT / "mechanisms/C4_formulations" / (name + ".json")
    if path.exists():
        return p1.read(path)
    public = p1.read(FROZEN / "checkpoints/public" / (name + ".json"))
    retrieved = p1.read(FROZEN / "retrieval" / (name + ".json"))["C2"]
    available = ids_from_sessions([{"utterances": doc["utterances"]} for doc in retrieved])
    context = {"case_id": public["case_id"], "checkpoint": name,
               "retrieved_public_history": retrieved, "last_completed_session": public["session"] - 1}
    result = generate_memory(secret, "on_demand", context, available, "C4_formulation:" + name)
    result.update(checkpoint=name, input_sha256=p1.digest(context), retrieved_ids=sorted(available))
    p1.write(path, result)
    return result


def response_context(public, memory):
    return {"previous_sessions": public["previous_sessions"],
            "current_dialogue": public["current_dialogue"], "latest_client": public["latest_client"],
            "formulation": memory, "session": public["session"]}


def generate_response(secret, name, condition):
    path = ROOT / "mechanisms/answers" / name / (condition + "_B.json")
    if path.exists():
        return
    public = p1.read(FROZEN / "checkpoints/public" / (name + ".json"))
    if condition == "C4":
        memory_record = c4_memory(secret, name)
        memory = memory_record["memory"]
    elif condition in ("C5", "C5_minus"):
        previous = public["session"] - 1
        memory_record = p1.read(ROOT / "mechanisms/C5_snapshots" /
                                ("%s_S%02d.json" % (public["case_id"], previous)))
        memory = dict(memory_record["memory"])
        if condition == "C5_minus":
            memory["unresolved_questions"] = []
    elif condition == "direct":
        memory_record = None
        memory = None
    else:
        raise ValueError(condition)
    context = response_context(public, memory)
    context_path = ROOT / "mechanisms/response_inputs" / name / (condition + ".json")
    p1.write(context_path, context)
    raw, fingerprint, usage, latency = p1.api(secret, p1.ANSWER_MODEL, RESPONSE_PROMPT, context,
                                              "mechanism_response:" + name + ":" + condition,
                                              700, 0.2)
    p1.write(path, {"checkpoint": name, "condition": condition, "task": "B",
                    "input_sha256": p1.digest(context), "request_sha256": fingerprint,
                    "memory_sha256": p1.digest(memory), "memory_source": None if memory_record is None else
                    memory_record.get("request_sha256"), "raw_text": raw,
                    "usage": usage, "latency_seconds": latency, "model": p1.ANSWER_MODEL})
    print(name, condition, "response", flush=True)


def make_memory_answers():
    for name in p1.NODES:
        public = p1.read(FROZEN / "checkpoints/public" / (name + ".json"))
        for condition in ("C4", "C5"):
            source = (ROOT / "mechanisms/C4_formulations" / (name + ".json") if condition == "C4" else
                      ROOT / "mechanisms/C5_snapshots" /
                      ("%s_S%02d.json" % (public["case_id"], public["session"] - 1)))
            record = p1.read(source)
            p1.write(ROOT / "mechanisms/answers" / name / (condition + "_A.json"),
                     {"checkpoint": name, "condition": condition, "task": "A",
                      "source": str(source.relative_to(ROOT)),
                      "raw_text": json.dumps(record["memory"], ensure_ascii=False),
                      "memory_sha256": p1.digest(record["memory"]), "model": p1.ANSWER_MODEL})


def run_parallel(fn, items, workers):
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fn, item) for item in items]
        for future in concurrent.futures.as_completed(futures):
            future.result()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("rolling", "c4", "responses", "all"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    secret = os.environ.get("DASHSCOPE_API_KEY") or sys.stdin.readline().strip()
    if not secret:
        raise RuntimeError("Missing DASHSCOPE_API_KEY")
    if args.phase in ("rolling", "all"):
        run_parallel(lambda cid: rolling_case(secret, cid), ["P%02d" % i for i in range(1, 9)], args.workers)
    if args.phase in ("c4", "all"):
        run_parallel(lambda name: c4_memory(secret, name), p1.NODES, args.workers)
    if args.phase in ("responses", "all"):
        conditions = [(name, condition) for name in p1.NODES for condition in ("C4", "C5", "C5_minus")]
        conditions += [(name, "direct") for name in DIRECT_NODES]
        run_parallel(lambda item: generate_response(secret, *item), conditions, args.workers)
        make_memory_answers()


if __name__ == "__main__":
    main()
