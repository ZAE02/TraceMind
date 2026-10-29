"""P1 blinded evaluation and mechanism comparison on frozen P0 material."""
import argparse
import concurrent.futures
import hashlib
import json
import os
import random
import re
import shutil
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent
P0 = ROOT.parent / "p0_understanding_in_action"
FROZEN = ROOT / "frozen_p0"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/responses"
ANSWER_MODEL = "deepseek-v4-pro-0813"
JUDGE_MODEL = "qwen3.7-flash-2026-07-15"
LOCK = threading.RLock()
NODES = ["P%02d_CP%d" % (i, j) for i in range(1, 9) for j in range(1, 4)]
CONDITIONS = ("C0", "C1", "C2", "C3")
MEMORY_FIELDS = ("established_understanding", "evolving_patterns", "unresolved_questions", "supporting_evidence_ids")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def log(record):
    path = ROOT / "logs/calls.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK:
        with path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_json(text):
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value)
    return json.loads(value)


def api(secret, model, system, content, purpose, max_output=3000, temperature=0.2):
    payload = {"model": model, "input": [{"role": "system", "content": system},
                                          {"role": "user", "content": json.dumps(content, ensure_ascii=False)}],
               "stream": False, "store": False, "temperature": temperature,
               "reasoning": {"effort": "none"}, "max_output_tokens": max_output}
    fingerprint = digest(payload)
    for attempt in range(3):
        started = time.monotonic()
        req = request.Request(BASE_URL, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              {"Authorization": "Bearer " + secret, "Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=180) as res:
                body = json.load(res)
            if body.get("status") != "completed":
                log({"at": now(), "purpose": purpose, "model": model, "request_sha256": fingerprint,
                     "status": "incomplete", "usage": body.get("usage"), "attempt": attempt + 1})
                if attempt < 2:
                    payload["max_output_tokens"] *= 2
                    fingerprint = digest(payload)
                    continue
                raise RuntimeError("Incomplete response: " + purpose)
            raw = "\n".join(part.get("text", "") for item in body.get("output", [])
                            if item.get("type") == "message" for part in item.get("content", [])
                            if part.get("type") == "output_text").strip()
            if not raw:
                raise RuntimeError("Empty response: " + purpose)
            seconds = round(time.monotonic() - started, 3)
            log({"at": now(), "purpose": purpose, "model": model, "request_sha256": fingerprint,
                 "status": "success", "attempt": attempt + 1, "usage": body.get("usage"),
                 "latency_seconds": seconds, "response_id": body.get("id")})
            return raw, fingerprint, body.get("usage"), seconds
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").replace(secret, "[REDACTED]")
            log({"at": now(), "purpose": purpose, "model": model, "request_sha256": fingerprint,
                 "status": "http_error", "code": exc.code, "detail": detail[:500], "attempt": attempt + 1})
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError("API HTTP %s: %s" % (exc.code, detail[:250]))
        except (error.URLError, TimeoutError, OSError) as exc:
            log({"at": now(), "purpose": purpose, "model": model, "request_sha256": fingerprint,
                 "status": "network_error", "detail": str(exc).replace(secret, "[REDACTED]")[:300],
                 "attempt": attempt + 1})
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def freeze():
    sources = []
    for relative in ("dialogues", "checkpoints/public", "checkpoints/annotation_drafts", "answers", "inputs"):
        sources += [p for p in (P0 / relative).rglob("*.json") if p.is_file()]
    sources += [p for p in (P0 / "retrieval").glob("P*_CP*.json")]
    sources += [P0 / "review/internal_blind_key.json"]
    manifest = {}
    for source in sources:
        relative = source.relative_to(P0)
        target = FROZEN / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target)
        if file_digest(source) != file_digest(target):
            raise RuntimeError("P0 changed after freeze: " + str(relative))
        manifest[str(relative).replace("\\", "/")] = file_digest(target)
    expected = {"dialogues": 64, "checkpoints/public": 24,
                "checkpoints/annotation_drafts": 24, "answers": 192,
                "inputs": 96}
    for prefix, count in expected.items():
        actual = sum(k.startswith(prefix + "/") for k in manifest)
        if actual != count:
            raise RuntimeError("Freeze count %s: %s != %s" % (prefix, actual, count))
    old = ROOT / "frozen_manifest.json"
    if old.exists() and read(old)["files"] != manifest:
        raise RuntimeError("Frozen manifest changed")
    write(old, {"created_at": read(old)["created_at"] if old.exists() else now(),
                "source": str(P0), "files": manifest, "sha256": digest(manifest)})
    mapping = {}
    for name in NODES:
        source = read(FROZEN / "review/internal_blind_key.json")[name]
        rng = random.Random("P1-evaluation-v1-" + name)
        labels = list("WXYZ")
        rng.shuffle(labels)
        mapping[name] = {labels[i]: source[letter] for i, letter in enumerate(sorted(source))}
        packet = {"checkpoint": name, "public_input": read(FROZEN / "checkpoints/public" / (name + ".json")),
                  "candidates": {label: {task: read(FROZEN / "answers" / name / (condition + "_" + task + ".json"))["raw_text"]
                                          for task in ("A", "B")}
                                 for label, condition in mapping[name].items()}}
        write(ROOT / "blind_packets" / (name + ".json"), packet)
    key = ROOT / "internal/condition_key.json"
    if key.exists() and read(key) != mapping:
        raise RuntimeError("Blind key changed")
    write(key, mapping)
    print("Frozen", len(manifest), "files; 24 blinded packets", flush=True)


ANNOTATION_SYSTEM = ("你是独立研究评审。只依据给出的冻结前公开咨询历史，核对标注草稿。不要读取或猜测隐藏人物轨迹。"
                     "只输出JSON对象，字段为 valid（布尔）、issues（字符串数组）、established、unresolved、"
                     "useful_exploration、unsupported_assumptions。后四项均为对象数组，"
                     "每项 {text:字符串,evidence_ids:字符串数组}。修正过度断言、未来信息和错误引用；"
                     "四个数组字段必须全部出现；若确实没有无依据预设，可令unsupported_assumptions为空数组。"
                     "可保留多种合理探索方向。不要求唯一标准咨询回复。")


def visible_ids(public):
    ids = {x["id"] for s in public["previous_sessions"] for x in s["utterances"]}
    ids.update(x["id"] for x in public["current_dialogue"])
    ids.add(public["latest_client"]["id"])
    return ids


def validate_annotation(secret, name):
    path = ROOT / "validated_annotations" / (name + ".json")
    if path.exists():
        return
    public = read(FROZEN / "checkpoints/public" / (name + ".json"))
    draft = read(FROZEN / "checkpoints/annotation_drafts" / (name + ".json"))
    content = {"public_history": public, "draft": {k: draft[k] for k in
               ("established", "unresolved", "useful_exploration", "unsupported_assumptions")}}
    raw, fingerprint, usage, latency = api(secret, JUDGE_MODEL, ANNOTATION_SYSTEM, content,
                                           "annotation_review:" + name, 5500)
    parsed = parse_json(raw)
    for field in ("established", "unresolved", "useful_exploration", "unsupported_assumptions"):
        if not isinstance(parsed.get(field), list) or (field != "unsupported_assumptions" and not parsed[field]):
            raise RuntimeError("Invalid annotation field " + name + field)
        for row in parsed[field]:
            if not isinstance(row.get("text"), str) or not isinstance(row.get("evidence_ids"), list):
                raise RuntimeError("Invalid annotation row " + name)
            if not set(row["evidence_ids"]) <= visible_ids(public):
                raise RuntimeError("Annotation cites unavailable evidence " + name)
    write(path, {"checkpoint": name, "review_model": JUDGE_MODEL,
                 "source_draft_sha256": file_digest(FROZEN / "checkpoints/annotation_drafts" / (name + ".json")),
                 "public_sha256": digest(public), "request_sha256": fingerprint,
                 "raw_text": raw, "validated_reference": parsed, "usage": usage, "latency_seconds": latency,
                 "status": "model_reviewed_not_human_gold"})
    print("annotation", name, flush=True)


RUBRIC = """六个0–4维度只按适用任务评分。0=明显错误/严重问题，1=关键内容缺失或多处错误，2=部分正确但遗漏重要内容，3=基本充分且仅有小缺口，4=充分、准确、贴合当前节点。独立行为锚点：
Task A Evidence Grounding：0无依据或与公开证据冲突；1多处断言无据；2事实与推测混杂；3主要判断有具体发言支持；4每项关键理解均可回溯到发言且不把咨询师猜测当事实。
Task A Progress Understanding：0颠倒/否认已取得认识；1忽略关键前后变化；2提及历史但未说明被确认、限制或修正的部分；3识别主要咨询进展和范围；4准确连接不同会话中的形成、验证、修正与仍未完成的认识。
Task A Uncertainty Awareness：0把关键未知当确定；1遗漏主要待澄清点；2承认不确定但笼统；3明确重要未知且不强行定论；4精确区分已知、假设及可继续探索的问题。
Task B History Utilization：0与历史明显冲突或机械重问已解决点；1几乎无视高度相关进展；2仅泛泛联系历史；3恰当地承接一条重要认识；4自然且准确地利用相关进展又不复述病历。历史不相关时，不引用历史也可得高分。
Task B Exploration Value：0封闭或误导探索；1回避显著待澄清点；2有探索但泛泛或偏题；3为当前关键不确定性留下表达空间；4具体而开放地推进理解，适当情绪反映也可得高分，不以问句数量评分。
Task B Response Appropriateness：0严重不当；1武断或不自然；2基本可接受但有明显预设/表达生硬；3自然尊重、少有预设；4语言、情绪回应与证据边界均贴合。
三个错误仅在有具体原文和历史证据时判true：unsupported_presupposition=把未证实或受挑战解释当作回应前提；redundant_exploration=无新理由重复已充分澄清的问题；missed_progress=忽略明显相关的已获认识。缺少历史引用本身不构成错误。若答题输入没有该历史，标记access_limited=true，不能把输入能力限制写成不当行为。"""


JUDGE_A = ("你是独立盲评员。候选编号不表示历史条件。只评这一份 Task A，不比较其他候选。"
           "参照公开对话和经独立模型核对的参考标注，允许合理替代解释；不要机械匹配文字。"
           "只输出JSON：scores含evidence_grounding、progress_understanding、uncertainty_awareness三个整数0–4；"
           "每项另有brief_reason字符串；confidence为high/medium/low；issues数组。\n" + RUBRIC)
JUDGE_B = ("你是独立盲评员。候选编号不表示历史条件。只评这一份 Task B，不比较其他候选。"
           "参考公开对话及经独立模型核对的标注，但不要把参考探索方向当唯一标准台词。"
           "只输出JSON：scores含history_utilization、exploration_value、response_appropriateness三个整数0–4；"
           "每项另有brief_reason字符串；errors含unsupported_presupposition、redundant_exploration、missed_progress，"
           "各为{occurred:布尔,trigger_quote:字符串或null,evidence_ids:字符串数组,access_limited:布尔}；"
           "confidence为high/medium/low；issues数组。\n" + RUBRIC)


def judge_one(secret, name, label, task, second=False):
    path = ROOT / ("second_reviews" if second else "reviews") / name / (label + "_" + task + ".json")
    if path.exists():
        return
    packet = read(ROOT / "blind_packets" / (name + ".json"))
    public = packet["public_input"]
    reference = read(ROOT / "validated_annotations" / (name + ".json"))["validated_reference"]
    condition = read(ROOT / "internal/condition_key.json")[name][label]
    actual_input = read(FROZEN / "inputs" / name / (condition + ".json"))
    actual_input.pop("case_id", None)
    actual_input.pop("checkpoint", None)
    context = {"public_history": public, "reference": {k: reference[k] for k in
               ("established", "unresolved", "useful_exploration", "unsupported_assumptions")},
               "candidate_id": label, "candidate_visible_input": actual_input,
               "candidate_answer": packet["candidates"][label][task], "task": task,
               "review_pass": 2 if second else 1}
    system = (JUDGE_A if task == "A" else JUDGE_B) + (" 独立复评；请重新判断，不参考第一次分数。" if second else "")
    fields = ("evidence_grounding", "progress_understanding", "uncertainty_awareness") if task == "A" else (
              "history_utilization", "exploration_value", "response_appropriateness")
    for retry in range(3):
        prompted = dict(context)
        if retry:
            prompted["format_correction"] = ("前次输出不合格。所有分数必须是0到4整数；若错误发生，"
                                              "必须引用候选回复的逐字片段以及本次公开历史中真实存在的发言ID。")
        raw, fingerprint, usage, latency = api(secret, JUDGE_MODEL, system, prompted,
                                               ("second_review:" if second else "review:") + name + ":" + label + ":" + task +
                                               (":retry%d" % retry if retry else ""),
                                               2200, 0.35 if second else 0.2)
        try:
            parsed = parse_json(raw)
            scores = parsed.get("scores")
            if not isinstance(scores, dict) or any(not isinstance(scores.get(k), int) or not 0 <= scores[k] <= 4 for k in fields):
                raise ValueError("Invalid judge score")
            if task == "B":
                for key in ("unsupported_presupposition", "redundant_exploration", "missed_progress"):
                    err = parsed.get("errors", {}).get(key)
                    if not isinstance(err, dict) or not isinstance(err.get("occurred"), bool):
                        raise ValueError("Invalid error annotation " + key)
                    if err["occurred"] and (not err.get("trigger_quote") or not err.get("evidence_ids")):
                        raise ValueError("Missing error evidence " + key)
                    if not set(err.get("evidence_ids", [])) <= visible_ids(public):
                        raise ValueError("Judge cites unavailable evidence " + key)
            break
        except (ValueError, TypeError, AttributeError) as exc:
            write(ROOT / "invalid_reviews" / name / (label + "_" + task + "_attempt%d.json" % (retry + 1)),
                  {"raw_text": raw, "request_sha256": fingerprint, "reason": str(exc), "usage": usage})
    else:
        raise RuntimeError("Could not obtain valid judge result: " + name + label + task)
    write(path, {"checkpoint": name, "candidate_id": label, "task": task, "review_pass": 2 if second else 1,
                 "blind": True, "review_model": JUDGE_MODEL, "request_sha256": fingerprint,
                 "public_sha256": digest(public), "candidate_input_sha256": digest(actual_input),
                 "candidate_answer_sha256": digest(packet["candidates"][label][task]),
                 "raw_text": raw, "assessment": parsed, "usage": usage, "latency_seconds": latency})


def second_review_targets():
    targets = []
    for name in NODES:
        for label in "WXYZ":
            for task in "AB":
                row = read(ROOT / "reviews" / name / (label + "_" + task + ".json"))["assessment"]
                scores = row["scores"]
                flagged = row.get("confidence") == "low" or 0 in scores.values() or (max(scores.values()) - min(scores.values()) >= 3)
                if task == "B" and any(x.get("occurred") for x in row.get("errors", {}).values()):
                    flagged = True
                # Fixed 20% safety sample across the full 192 responses.
                sample = int(hashlib.sha256((name + label + task).encode()).hexdigest()[:8], 16) % 5 == 0
                if flagged or sample:
                    targets.append((name, label, task))
    write(ROOT / "second_review_targets.json", {"selection": "low confidence, severe/uneven scores, observable error, or fixed 20% hash sample",
                                                   "targets": [list(x) for x in targets]})
    return targets


def run_parallel(fn, items, workers):
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fn, item) for item in items]
        for future in concurrent.futures.as_completed(futures):
            future.result()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("freeze", "annotations", "review", "second_review", "all"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.phase in ("freeze", "all"):
        freeze()
    secret = os.environ.get("DASHSCOPE_API_KEY") or (sys.stdin.readline().strip() if args.phase != "freeze" else "")
    if args.phase != "freeze" and not secret:
        raise RuntimeError("Missing DASHSCOPE_API_KEY")
    if args.phase in ("annotations", "all"):
        run_parallel(lambda name: validate_annotation(secret, name), NODES, args.workers)
    if args.phase in ("review", "all"):
        run_parallel(lambda item: judge_one(secret, *item),
                     [(name, label, task) for name in NODES for label in "WXYZ" for task in "AB"], args.workers)
    if args.phase in ("second_review", "all"):
        run_parallel(lambda item: judge_one(secret, *item, second=True), second_review_targets(), args.workers)


if __name__ == "__main__":
    main()
