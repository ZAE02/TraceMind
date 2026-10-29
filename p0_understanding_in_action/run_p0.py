"""Independent, resumable P0 synthetic counseling experiment (Python 3.9+)."""
import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import random
import re
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent
ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
GEN_MODEL = "deepseek-v4-pro-0813"
EMBED_MODEL = "qwen3.7-text-embedding-flash"
LOCK = threading.RLock()
SEEDS = [
    ("P01", "Stable", "设计师", "工作反馈后的反复确认", "朋友间的临时安排"),
    ("P02", "Stable", "社区护士", "照顾家人与个人休息", "团队排班变化"),
    ("P03", "Gradual", "研究生", "公开表达与评价担忧", "与伴侣的沟通"),
    ("P04", "Gradual", "餐厅经理", "职责边界与求助", "家中长辈的身体变化"),
    ("P05", "Fluctuating", "软件测试工程师", "工作压力下的退缩和恢复", "与旧友重新联系"),
    ("P06", "Fluctuating", "小学教师", "家庭照顾与内疚", "职业选择"),
    ("P07", "Reinterpretive", "自由插画师", "减少社交参与的原因变化", "收入不稳定"),
    ("P08", "Reinterpretive", "物流调度员", "对同事帮助的理解变化", "孩子入学安排"),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_log(value):
    path = ROOT / "logs/calls.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK:
        with path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(value, ensure_ascii=False) + "\n")


def api(secret, model, input_value, purpose, max_output=768, temperature=0.7, reasoning="low"):
    payload = {"model": model, "input": input_value}
    if model == GEN_MODEL:
        payload.update(store=False, stream=False, max_output_tokens=max_output)
        payload.update(temperature=temperature, reasoning={"effort": reasoning})
    fingerprint = sha(payload)
    for attempt in range(3):
        req = request.Request(ENDPOINT + ("/embeddings" if model == EMBED_MODEL else "/responses"),
                              json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              {"Authorization": "Bearer " + secret, "Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=180) as res:
                body = json.load(res)
            record = {"at": now(), "purpose": purpose, "model": model,
                      "request_sha256": fingerprint, "status": "success", "attempt": attempt + 1,
                      "usage": body.get("usage"), "response_id": body.get("id")}
            append_log(record)
            return body, fingerprint
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").replace(secret, "[REDACTED]")
            append_log({"at": now(), "purpose": purpose, "model": model,
                        "request_sha256": fingerprint, "status": "http_error",
                        "http_status": exc.code, "attempt": attempt + 1, "detail": detail[:500]})
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError("API HTTP %s: %s" % (exc.code, detail[:300]))
        except (error.URLError, TimeoutError, OSError) as exc:
            append_log({"at": now(), "purpose": purpose, "model": model,
                        "request_sha256": fingerprint, "status": "network_error",
                        "attempt": attempt + 1, "detail": str(exc).replace(secret, "[REDACTED]")[:300]})
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def text_response(body):
    if body.get("status") != "completed":
        raise RuntimeError("Incomplete API response: " + str(body.get("status")))
    parts = [part.get("text", "") for item in body.get("output", [])
             if item.get("type") == "message" for part in item.get("content", [])
             if part.get("type") == "output_text"]
    result = "\n".join(parts).strip()
    if not result:
        raise RuntimeError("Empty model response")
    return result


def ask(secret, system, context, purpose, max_output=768, temperature=0.7, reasoning="low"):
    payload = [{"role": "system", "content": system},
               {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
    for limit in (max_output, max_output * 2, max_output * 4):
        body, fingerprint = api(secret, GEN_MODEL, payload, purpose, limit, temperature, reasoning)
        if body.get("status") == "completed":
            return text_response(body), fingerprint, body.get("usage")
        append_log({"at": now(), "purpose": purpose, "status": "incomplete_response",
                    "request_sha256": fingerprint, "limit": limit})
    raise RuntimeError("Incomplete API response after larger output limits: " + purpose)


def parse_json(raw):
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
    return json.loads(cleaned)


def generate_plan(secret, seed):
    cid, trajectory, job, theme, other = seed
    path = ROOT / "plans" / (cid + ".json")
    if path.exists():
        return read(path)
    log_path = ROOT / "logs/calls.jsonl"
    if log_path.exists():
        for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
            record = json.loads(line)
            if record.get("purpose") == "plan:" + cid and record.get("status") == "invalid_json" and record.get("raw"):
                try:
                    recovered = parse_json(record["raw"])
                    recovered = recovered.get("required_json", recovered)
                    if len(recovered["sessions"]) == 8:
                        recovered.update(case_id=cid, synthetic=True, trajectory=trajectory,
                                         source_request_sha256=record["request_sha256"], model=GEN_MODEL,
                                         recovered_from_log=True)
                        write(path, recovered)
                        return recovered
                except (ValueError, KeyError, TypeError):
                    pass
    system = ("你是拟真纵向心理咨询数据的研究策划者。只输出有效JSON对象。设计完全虚构的成人来访者；"
              "不要使用诊断标签或真实患者资料。提供稳定人物背景，以及8次每周会谈的具体生活事件卡。"
              "心理变化模板只用于规划，不要把模板名称写进人物发言。每次会谈应有发生过的事件、私下体验、尚未说出的细节；"
              "允许不确定、反复和修正，避免机械逐周进步。不同会谈之间事实和时间必须一致。")
    context = {"case_id": cid, "trajectory": trajectory, "occupation": job,
               "primary_theme": theme, "secondary_theme": other,
               "required_json": {"profile": {"name": "", "age": 30, "occupation": job,
                                             "living_context": "", "relationships": "", "speech_style": ""},
                                 "private_background": [""],
                                 "sessions": [{"session": n, "occurred_events": [""],
                                               "private_experiences": [""],
                                               "still_unspoken": [""]} for n in range(1, 9)]}}
    for variant in range(2):
        raw, fingerprint, usage = ask(secret, system, dict(context, retry=variant),
                                      "plan:" + cid, 12000, 0.7)
        try:
            obj = parse_json(raw)
            obj = obj.get("required_json", obj)
            if len(obj["sessions"]) != 8 or sorted(x["session"] for x in obj["sessions"]) != list(range(1, 9)):
                raise ValueError("Plan must have sessions 1-8")
            obj.update(case_id=cid, synthetic=True, trajectory=trajectory, source_request_sha256=fingerprint,
                       model=GEN_MODEL, usage=usage)
            write(path, obj)
            return obj
        except (ValueError, KeyError, TypeError) as exc:
            append_log({"at": now(), "purpose": "plan:" + cid, "status": "invalid_json",
                        "request_sha256": fingerprint, "reason": str(exc), "raw": raw[:10000]})
    raise RuntimeError("Could not generate valid plan for " + cid)


CLIENT_PROMPT = ("你是完全虚构的成人来访者，正在进行持续的个人咨询。只输出本轮来访者自然发言，1至4句；"
                 "不要写角色名、解释或舞台说明。严格依照人物私有事实和实际对话，不要提前透露全部细节，"
                 "但可自然表达当前想法。尚未做的事不能写成已做，不要凭空造出已发生的重大事件。"
                 "面对开放回应也可主动说明重要体验；不必总是顺从咨询师的解释。")
COUNSELOR_PROMPT = ("你是个人咨询中的咨询师。只输出本轮实际对来访者说的话，1至3句，不写角色名、分析报告或舞台说明。"
                    "只能依据已公开的对话，不知道研究者计划或来访者私有事实。回应当前体验，必要时澄清，"
                    "不把暂时解释当成事实，不反复盘问微小细节。允许不确定与修正；避免临床诊断和保证式承诺。")


def public_rows(rows):
    return [{"id": x["id"], "role": x["role"], "text": x["text"]} for x in rows]


def generate_case(secret, seed):
    plan = generate_plan(secret, seed)
    cid = seed[0]
    for session in range(1, 9):
        path = ROOT / "dialogues" / cid / ("S%02d.json" % session)
        obj = read(path) if path.exists() else {"case_id": cid, "session": session, "synthetic": True,
                                               "utterances": [], "status": "running"}
        rows = obj["utterances"]
        previous = [read(ROOT / "dialogues" / cid / ("S%02d.json" % n)) for n in range(1, session)]
        if any(x["status"] != "completed" for x in previous):
            raise RuntimeError("Previous session incomplete: " + cid)
        card = plan["sessions"][session - 1]
        while len(rows) < 16:
            role = "client" if len(rows) % 2 == 0 else "counselor"
            visible = {"current_session": session,
                       "previous_sessions": [{"session": x["session"], "utterances": public_rows(x["utterances"])} for x in previous],
                       "current_dialogue": public_rows(rows),
                       "time_notice": "前半段继续自然探索" if len(rows) < 10 else "接近本次结束，可自然收束但不强求结论"}
            if role == "client":
                visible.update(client_profile=plan["profile"], private_background=plan["private_background"],
                               occurred_events=[{"session": x["session"], "events": x["occurred_events"]}
                                                for x in plan["sessions"][:session]],
                               current_private_experiences=card["private_experiences"],
                               current_still_unspoken=card["still_unspoken"])
            system = CLIENT_PROMPT if role == "client" else COUNSELOR_PROMPT
            try:
                spoken, fingerprint, usage = ask(secret, system, visible,
                                                  "dialogue:%s:S%02d:%02d" % (cid, session, len(rows) + 1),
                                                  1024, 0.7)
                if re.match(r"^(来访者|咨询师|Client|Counselor)\s*[:：]", spoken, re.I):
                    spoken = re.sub(r"^(来访者|咨询师|Client|Counselor)\s*[:：]\s*", "", spoken, flags=re.I)
                if not spoken or "<think>" in spoken:
                    raise RuntimeError("Invalid spoken output")
                rows.append({"id": "%s-S%02d-U%02d" % (cid, session, len(rows) + 1),
                             "role": role, "text": spoken, "request_sha256": fingerprint, "usage": usage})
                write(path, obj)
            except Exception:
                write(path, obj)
                raise
        obj["status"] = "completed"
        write(path, obj)
        print(cid, "S%02d" % session, "completed", flush=True)


def freeze_case(cid):
    plan = read(ROOT / "plans" / (cid + ".json"))
    for session in (3, 5, 7):
        cp = {3: "CP1", 5: "CP2", 7: "CP3"}[session]
        previous = [read(ROOT / "dialogues" / cid / ("S%02d.json" % n)) for n in range(1, session)]
        current = read(ROOT / "dialogues" / cid / ("S%02d.json" % session))
        # A mid-session client turn: enough local dialogue exists, but the future stays sealed.
        freeze_index = 8
        assert current["utterances"][freeze_index]["role"] == "client"
        public = {"case_id": cid, "checkpoint": cp, "session": session, "synthetic": True,
                  "previous_sessions": [{"session": x["session"], "utterances": public_rows(x["utterances"])} for x in previous],
                  "current_dialogue": public_rows(current["utterances"][:freeze_index]),
                  "latest_client": public_rows([current["utterances"][freeze_index]])[0]}
        private = {"case_id": cid, "checkpoint": cp, "profile": plan["profile"],
                   "private_background": plan["private_background"],
                   "occurred_events": plan["sessions"][:session],
                   "future_dialogue": public_rows(current["utterances"][freeze_index + 1:]),
                   "notice": "Researcher-only synthetic settings; not objective clinical truth."}
        path = ROOT / "checkpoints/public" / (cid + "_" + cp + ".json")
        if path.exists() and sha(read(path)) != sha(public):
            raise RuntimeError("Frozen public input changed: " + str(path))
        write(path, public)
        write(ROOT / "checkpoints/private" / (cid + "_" + cp + ".json"), private)


ANNOTATION_PROMPT = ("仅根据给出的公开、已发生咨询对话，撰写咨询进展标注草稿。你不知道来访者私有设定和未来发言。"
                     "只输出有效JSON对象，字段为 established、unresolved、useful_exploration、unsupported_assumptions，"
                     "各字段为条目数组；每个条目格式为 {text:字符串,evidence_ids:字符串数组}。"
                     "引用必须是输入中真实可见的发言ID；不把咨询师的猜测当作来访者确认的事实；允许多种合理回应。")


def annotate(secret, cid):
    for cp in ("CP1", "CP2", "CP3"):
        name = cid + "_" + cp
        path = ROOT / "checkpoints/annotation_drafts" / (name + ".json")
        if path.exists():
            continue
        public = read(ROOT / "checkpoints/public" / (name + ".json"))
        visible_ids = {x["id"] for s in public["previous_sessions"] for x in s["utterances"]}
        visible_ids.update(x["id"] for x in public["current_dialogue"])
        visible_ids.add(public["latest_client"]["id"])
        for retry in range(2):
            raw, fingerprint, usage = ask(secret, ANNOTATION_PROMPT, public, "annotation:" + name,
                                          2300, 0.2, "none")
            try:
                obj = parse_json(raw)
                for key in ("established", "unresolved", "useful_exploration", "unsupported_assumptions"):
                    if not isinstance(obj[key], list) or not obj[key]:
                        raise ValueError("Missing " + key)
                    for entry in obj[key]:
                        if not isinstance(entry["text"], str) or not isinstance(entry["evidence_ids"], list):
                            raise ValueError("Bad entry")
                        if not set(entry["evidence_ids"]) <= visible_ids:
                            raise ValueError("Cites unavailable evidence")
                obj.update(case_id=cid, checkpoint=cp, review_status="unreviewed_model_draft",
                           request_sha256=fingerprint, public_sha256=sha(public), usage=usage)
                write(path, obj)
                break
            except (ValueError, KeyError, TypeError) as exc:
                append_log({"at": now(), "purpose": "annotation:" + name, "status": "invalid",
                            "request_sha256": fingerprint, "reason": str(exc), "raw": raw[:10000]})
        else:
            raise RuntimeError("Could not annotate " + name)


def embed(secret, text, purpose):
    path = ROOT / "retrieval/cache_v2" / (sha(text) + ".json")
    if path.exists():
        return read(path)["vector"]
    body, fingerprint = api(secret, EMBED_MODEL, text, purpose, 64)
    vector = body["data"][0]["embedding"]
    write(path, {"vector": vector, "text_sha256": sha(text), "request_sha256": fingerprint})
    return vector


def embed_many(secret, texts, purpose):
    vectors = [None] * len(texts)
    missing = []
    for i, text in enumerate(texts):
        path = ROOT / "retrieval/cache_v2" / (sha(text) + ".json")
        if path.exists():
            vectors[i] = read(path)["vector"]
        else:
            missing.append((i, text, path))
    for start in range(0, len(missing), 20):
        batch = missing[start:start + 20]
        body, fingerprint = api(secret, EMBED_MODEL, [x[1] for x in batch], purpose, 64)
        if len(body["data"]) != len(batch):
            raise RuntimeError("Embedding response count mismatch")
        # This provider currently returns index=0 for every vector in a batch;
        # response order matches input order, verified by the API probe.
        for offset, row in enumerate(body["data"]):
            i, text, path = batch[offset]
            vectors[i] = row["embedding"]
            write(path, {"vector": vectors[i], "text_sha256": sha(text),
                         "request_sha256": fingerprint})
    return vectors


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / ((math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1)


def retrieval(secret, public):
    name = public["case_id"] + "_" + public["checkpoint"]
    path = ROOT / "retrieval" / (name + ".json")
    if path.exists():
        return read(path)
    docs = []
    for session in public["previous_sessions"]:
        rows = session["utterances"]
        for i in range(0, len(rows), 2):
            docs.append({"session": session["session"], "utterances": rows[i:i + 2]})
    query = json.dumps(public["current_dialogue"][-4:] + [public["latest_client"]], ensure_ascii=False)
    qvec = embed(secret, query, "embedding_query:" + name)
    texts = [json.dumps(doc, ensure_ascii=False) for doc in docs]
    vectors = embed_many(secret, texts, "embedding_docs:" + name)
    scored = []
    for i, doc in enumerate(docs):
        vector = vectors[i]
        scored.append({"index": i, "score": cosine(qvec, vector), "session": doc["session"]})
    ranked = sorted(scored, key=lambda x: (-x["score"], x["index"]))
    budget = 4500  # Character budget is deterministic and tokenizer independent.
    def choose(order):
        selected = []
        total = 0
        for item in order:
            doc = docs[item["index"]]
            length = len(json.dumps(doc, ensure_ascii=False))
            if total + length <= budget:
                selected.append(doc)
                total += length
        return sorted(selected, key=lambda x: (x["session"], x["utterances"][0]["id"]))
    standard = choose(ranked)
    anchors = []
    for number in sorted({1, public["session"] - 1}):
        subset = [x for x in ranked if x["session"] == number]
        if subset:
            anchors.append(subset[0])
    temporal = choose(anchors + [x for x in ranked if x not in anchors])
    output = {"public_sha256": sha(public), "query_sha256": sha(query), "ranking": ranked,
              "budget_characters": budget, "C1": standard, "C2": temporal,
              "algorithm_C1": "cosine embedding top-ranked pairs",
              "algorithm_C2": "first/latest session anchors then cosine top-ranked pairs; chronological display"}
    write(path, output)
    return output


TASK_A = ("仅根据提供的咨询记录回答。输出JSON对象，字段 established（已有证据支持的认识列表）、"
          "uncertain（仍不确定的问题列表）、evidence_ids（所用发言ID列表）。区分事实、来访者体验与咨询师猜测；"
          "不依据未知私有设定或未来。")
TASK_B = ("你是咨询师。仅根据提供的咨询记录，输出一条直接对来访者说的自然回应，1至3句；"
          "可以反映、澄清、追问或适度解释。不要写分析、角色名或推理过程；不把未证实的解释当成前提。")


def answer_node(secret, cid, cp):
    name = cid + "_" + cp
    public = read(ROOT / "checkpoints/public" / (name + ".json"))
    found = retrieval(secret, public)
    base = {"case_id": cid, "checkpoint": cp, "session": public["session"],
            "current_dialogue": public["current_dialogue"], "latest_client": public["latest_client"]}
    contexts = {
        "C0": dict(base, previous_sessions=[]),
        "C1": dict(base, retrieved_history=found["C1"]),
        "C2": dict(base, retrieved_history=found["C2"]),
        "C3": dict(base, previous_sessions=public["previous_sessions"]),
    }
    for condition, context in contexts.items():
        context_path = ROOT / "inputs" / name / (condition + ".json")
        if context_path.exists() and sha(read(context_path)) != sha(context):
            raise RuntimeError("Input changed: " + str(context_path))
        write(context_path, context)
        for task, prompt in (("A", TASK_A), ("B", TASK_B)):
            path = ROOT / "answers" / name / (condition + "_" + task + ".json")
            if path.exists():
                continue
            raw, fingerprint, usage = ask(secret, prompt, context,
                                          "answer:%s:%s:%s" % (name, condition, task),
                                          5000 if task == "A" else 1600, 0.2, "none")
            parsed = None
            if task == "A":
                try:
                    parsed = parse_json(raw)
                except ValueError:
                    pass
            write(path, {"case_id": cid, "checkpoint": cp, "condition": condition,
                         "task": task, "input_sha256": sha(context), "request_sha256": fingerprint,
                         "model": GEN_MODEL, "raw_text": raw, "parsed": parsed, "usage": usage,
                         "status": "completed" if task == "B" or isinstance(parsed, dict) else "format_invalid"})
    print(name, "answers completed", flush=True)


def review_forms():
    mapping = {}
    for cid, _, _, _, _ in SEEDS:
        for cp in ("CP1", "CP2", "CP3"):
            name = cid + "_" + cp
            rng = random.Random(name + "-blind-v1")
            letters = list("WXYZ")
            rng.shuffle(letters)
            mapping[name] = dict(zip(letters, ("C0", "C1", "C2", "C3")))
            packet_path = ROOT / "review/blind_packets" / (name + ".json")
            if all((ROOT / "answers" / name / (condition + "_" + task + ".json")).exists()
                   for condition in ("C0", "C1", "C2", "C3") for task in ("A", "B")):
                answers = {}
                for letter, condition in mapping[name].items():
                    answers[letter] = {
                        task: read(ROOT / "answers" / name / (condition + "_" + task + ".json"))["raw_text"]
                        for task in ("A", "B")}
                write(packet_path, {"checkpoint": name,
                                    "public_input": read(ROOT / "checkpoints/public" / (name + ".json")),
                                    "annotation_draft_unreviewed": read(ROOT / "checkpoints/annotation_drafts" / (name + ".json")),
                                    "candidate_answers": answers,
                                    "instruction": "Blind condition identities are stored separately in internal_blind_key.json; annotation is an unreviewed draft."})
            form = ROOT / "review/blank_answer_forms" / (name + ".json")
            if not form.exists():
                write(form, {"checkpoint": name, "reviewer": None, "reviewed_at": None,
                             "annotation_adequate": None, "multiple_reasonable_responses": None,
                             "conditions": {letter: {"task_A_grounded": None, "task_A_uncertainty": None,
                                                     "task_B_historical_relevance": None,
                                                     "task_B_exploration_value": None,
                                                     "task_B_non_presupposition": None,
                                                     "task_B_appropriateness": None,
                                                     "wrong_presupposition": None, "repeated_exploration": None,
                                                     "missed_progress": None, "notes": None}
                                            for letter in letters}})
    write(ROOT / "review/internal_blind_key.json", mapping)
    for cid, _, _, _, _ in SEEDS:
        for session in range(1, 9):
            path = ROOT / "review/blank_dialogue_forms" / ("%s_S%02d.json" % (cid, session))
            if not path.exists():
                write(path, {"case_id": cid, "session": session, "reviewer": None,
                             "naturalness": None, "continuity": None, "evidence_sufficiency": None,
                             "leakage": None, "notes": None})


def audit():
    issues = []
    count_sessions = count_checkpoints = count_answers = 0
    for cid, _, _, _, _ in SEEDS:
        for session in range(1, 9):
            path = ROOT / "dialogues" / cid / ("S%02d.json" % session)
            if not path.exists():
                issues.append("missing " + str(path.relative_to(ROOT)))
                continue
            obj = read(path)
            rows = obj["utterances"]
            if obj["status"] != "completed" or len(rows) != 16:
                issues.append("incomplete " + str(path.relative_to(ROOT)))
            if any(x["role"] != ("client" if i % 2 == 0 else "counselor") for i, x in enumerate(rows)):
                issues.append("role order " + str(path.relative_to(ROOT)))
            if obj["status"] == "completed" and len(rows) == 16:
                count_sessions += 1
        for cp in ("CP1", "CP2", "CP3"):
            name = cid + "_" + cp
            pubpath = ROOT / "checkpoints/public" / (name + ".json")
            annpath = ROOT / "checkpoints/annotation_drafts" / (name + ".json")
            if not pubpath.exists() or not annpath.exists():
                issues.append("missing checkpoint/annotation " + name)
                continue
            public, annotation = read(pubpath), read(annpath)
            ids = {x["id"] for s in public["previous_sessions"] for x in s["utterances"]}
            ids.update(x["id"] for x in public["current_dialogue"])
            ids.add(public["latest_client"]["id"])
            if any(not set(entry["evidence_ids"]) <= ids for key in
                   ("established", "unresolved", "useful_exploration", "unsupported_assumptions")
                   for entry in annotation[key]):
                issues.append("annotation future citation " + name)
            count_checkpoints += 1
            for condition in ("C0", "C1", "C2", "C3"):
                ipath = ROOT / "inputs" / name / (condition + ".json")
                if not ipath.exists():
                    issues.append("missing input " + name + condition)
                    continue
                context = read(ipath)
                if condition == "C0" and (context.get("previous_sessions") or context.get("retrieved_history")):
                    issues.append("C0 leaked history " + name)
                if condition == "C3" and sha(context.get("previous_sessions")) != sha(public["previous_sessions"]):
                    issues.append("C3 history mismatch " + name)
                if condition in ("C1", "C2"):
                    historical_ids = ids - {x["id"] for x in public["current_dialogue"]} - {public["latest_client"]["id"]}
                    used = {x["id"] for doc in context["retrieved_history"] for x in doc["utterances"]}
                    if not used <= historical_ids:
                        issues.append("retrieval future citation " + name + condition)
                for task in ("A", "B"):
                    apath = ROOT / "answers" / name / (condition + "_" + task + ".json")
                    if not apath.exists():
                        issues.append("missing answer " + name + condition + task)
                        continue
                    answer = read(apath)
                    if answer["input_sha256"] != sha(context):
                        issues.append("answer/input mismatch " + name + condition + task)
                    if answer.get("status") != "completed":
                        issues.append("answer not completed " + name + condition + task)
                    if task == "A":
                        cited = answer.get("parsed", {}).get("evidence_ids") if isinstance(answer.get("parsed"), dict) else None
                        allowed = {x["id"] for x in context["current_dialogue"]}
                        allowed.add(context["latest_client"]["id"])
                        allowed.update(x["id"] for s in context.get("previous_sessions", []) for x in s["utterances"])
                        allowed.update(x["id"] for d in context.get("retrieved_history", []) for x in d["utterances"])
                        if not isinstance(cited, list) or not set(cited) <= allowed:
                            issues.append("answer cites unavailable evidence " + name + condition)
                    count_answers += 1
    logs = ROOT / "logs/calls.jsonl"
    usage = Counter()
    if logs.exists():
        for line in logs.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("status") == "success" and isinstance(row.get("usage"), dict):
                u = row["usage"]
                usage["input_tokens"] += u.get("input_tokens", u.get("prompt_tokens", 0)) or 0
                usage["output_tokens"] += u.get("output_tokens", u.get("completion_tokens", 0)) or 0
            if "sk-ws-" in line:
                issues.append("possible secret in call log")
                break
    result = {"generated_at": now(), "case_plans": sum((ROOT / "plans" / (x[0] + ".json")).exists() for x in SEEDS),
              "completed_sessions": count_sessions, "checkpoints_with_annotations": count_checkpoints,
              "answer_files": count_answers, "expected": {"case_plans": 8, "sessions": 64,
              "checkpoints": 24, "answers": 192}, "usage_observed": dict(usage),
              "human_review_performed": False, "model_blind_review_performed": False,
              "issues": issues, "complete": not issues and (count_sessions, count_checkpoints, count_answers) == (64, 24, 192)}
    write(ROOT / "audit.json", result)
    return result


def run_parallel(fn, items, workers):
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, x): x for x in items}
        for future in concurrent.futures.as_completed(futures):
            future.result()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("plans", "dialogues", "freeze", "annotations", "answers", "audit", "all"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    secret = os.environ.get("DASHSCOPE_API_KEY") or (sys.stdin.readline().strip() if args.phase not in ("freeze", "audit") else "")
    if args.phase not in ("freeze", "audit") and not secret:
        raise RuntimeError("Missing DASHSCOPE_API_KEY")
    if args.phase in ("plans", "all"):
        run_parallel(lambda seed: generate_plan(secret, seed), SEEDS, args.workers)
    if args.phase in ("dialogues", "all"):
        run_parallel(lambda seed: generate_case(secret, seed), SEEDS, args.workers)
    if args.phase in ("freeze", "all"):
        for seed in SEEDS:
            freeze_case(seed[0])
    if args.phase in ("annotations", "all"):
        run_parallel(lambda seed: annotate(secret, seed[0]), SEEDS, args.workers)
    if args.phase in ("answers", "all"):
        run_parallel(lambda item: answer_node(secret, *item),
                     [(s[0], cp) for s in SEEDS for cp in ("CP1", "CP2", "CP3")], args.workers)
    if args.phase in ("audit", "all"):
        review_forms()
        result = audit()
        print(json.dumps({k: v for k, v in result.items() if k != "issues"}, ensure_ascii=False), flush=True)
        if result["issues"]:
            print("Audit issues:", result["issues"][:20], flush=True)


if __name__ == "__main__":
    main()
