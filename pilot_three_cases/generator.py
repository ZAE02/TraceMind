"""Two isolated role contexts, one utterance per Responses request (Python 3.9+)."""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent
CASE_IDS = ("C01", "C02", "C03")
ROLES = ("client", "counselor")
LOG_LOCK = threading.RLock()
COMPLETE_STATUSES = ("awaiting_human_review", "awaiting_model_review", "reviewed_by_model")


class GenerationError(Exception):
    pass


@contextmanager
def exclusive_run(root):
    lock = root / "generation_logs" / "runner.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock.open("x", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "started_at": now()}, stream)
    except FileExistsError:
        raise GenerationError("Another run owns runner.lock; verify that process has exited before removing a stale lock")
    try:
        yield
    finally:
        lock.unlink()


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def append_log(root, record, filename="full_agent_calls.jsonl"):
    if Path(filename).name != filename or not filename.endswith(".jsonl"):
        raise GenerationError("Invalid call log basename")
    path = root / "generation_logs" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOG_LOCK:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def scrub(value, secret):
    """Do not retain an authentication value even if a remote error echoes it."""
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]") if secret else value
    if isinstance(value, dict):
        return {key: scrub(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, secret) for item in value]
    return value


def card_for(plan, number):
    cards = [card for card in plan["session_cards"] if card["session_number"] == number]
    if len(cards) != 1:
        raise GenerationError("Missing or duplicate session card")
    return cards[0]


def public_history(root, case_id, number):
    history = []
    for previous in range(1, number):
        path = root / "dialogues" / case_id / ("S%02d.json" % previous)
        if not path.exists():
            raise GenerationError("Previous session is missing: " + str(path))
        data = read_json(path)
        if data["status"] not in COMPLETE_STATUSES or data["continuity_review"]["status"] != "passed":
            raise GenerationError("Previous session needs a continuity review: " + str(path))
        history.append({"session_number": previous, "utterances": visible_utterances(data["utterances"])})
    return history


def visible_utterances(utterances):
    return [{"id": row["id"], "role": row["role"], "text": row["text"]} for row in utterances]


def context_for(role, plan, number, previous, current, ledger):
    """Allowlist fields; never serialize a whole case plan or ledger into a role."""
    card = card_for(plan, number)
    context = {
        "session": {"number": number, "relative_week": card["relative_week"], "format": "个人咨询；来访者与咨询师身份保持一致",
                    "typical_interval_days": 7, "within_session_time": "同一场连续谈话；不离开谈话去执行工作任务"},
        "previous_session_dialogues": previous,
        "current_session_dialogue": visible_utterances(current),
    }
    pair_number = len(current) // 2 + 1
    if pair_number < 8:
        context["time_notice"] = "本次咨询时间仍充足，继续回应当前困扰或了解来访者当次还有什么想谈的，不提前告别。无需制造进步或强求心理结论。"
    if pair_number >= 9:
        context["time_notice"] = "本次咨询时间逐渐接近结束，可以回应尚未说完的内容并自然收尾，不必取得突破。"
    if pair_number == 12:
        context["time_notice"] = "本次咨询剩下最后一组交流，请自然结束；未解决的问题可以留待下次。"
    if role == "client":
        profile_fields = ("name", "age", "gender", "occupation", "living_context", "relationships", "speech_style")
        context["client_profile"] = {key: plan["client_profile"][key] for key in profile_fields}
        context["private_life_history"] = plan["private_life_history"]
        context["occurred_life_events"] = [
            {"session_number": item["session_number"], "life_events": item["life_events"]}
            for item in plan["session_cards"] if item["session_number"] < number
        ]
        context["current_session_card"] = {key: card[key] for key in ("life_events", "private_experiences", "still_unspoken_details")}
        context["confirmed_additional_facts"] = [
            {"fact": fact["fact"]} for fact in ledger.get("confirmed_additions", [])
            if fact["first_session"] < number
        ]
        context["turn_instruction"] = (
            "以本周发生的具体事情自然开启咨询，不要一次讲完全部细节。"
            if not current else "只回应咨询师刚才的实际发言；未被问到的细节不必主动全部说明。"
        )
    elif role == "counselor":
        if not current or current[-1]["role"] != "client":
            raise GenerationError("Counselor requires a latest client utterance")
    else:
        raise GenerationError("Unknown role")
    return context


def build_payload(role, prompt, config, context):
    payload = {
        "model": config["model"],
        "input": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        "store": False,
        "stream": False,
        "temperature": config["temperature"],
        "reasoning": {"effort": config.get("role_reasoning_effort", {}).get(role, config["reasoning_effort"])},
        "max_output_tokens": config["max_output_tokens"],
    }
    if role == "counselor" and "session" in context and config.get("counselor_process_instruction"):
        payload["input"].append({"role": "user", "content": config["counselor_process_instruction"]})
    if role == "client" and "session" in context and config.get("client_process_instruction"):
        payload["input"].append({"role": "user", "content": config["client_process_instruction"]})
    return payload


def extract_text(response):
    if response.get("status") != "completed":
        raise GenerationError("API response is not completed: " + str(response.get("status")))
    pieces = []
    for item in response.get("output", []):
        if item.get("type") == "message" and item.get("role") == "assistant":
            if item.get("status", "completed") != "completed":
                raise GenerationError("Incomplete message")
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    pieces.append(content.get("text", ""))
    text = "\n".join(pieces).strip()
    if not text:
        raise GenerationError("API returned no spoken text")
    if re.search(r"(^|\n)\s*(来访者|咨询师|Client|Counselor)\s*[:：]", text, re.I):
        raise GenerationError("Role label or scripted dialogue in output; preserve call for review")
    if re.search(r"<think>|LATENT_TRAJECTORY|GOLD_INTERPRETATION|EXPECTED_RESPONSE", text, re.I):
        raise GenerationError("Role output includes analysis or research fields")
    if re.search(r"[（(].*(本次会话已结束|对话结束|咨询结束).*[）)]", text):
        raise GenerationError("Stage direction is not spoken consultation text")
    return text


def call_api(root, config, payload, call_context, secret, transport=None, sleep=time.sleep):
    transport = transport or request.urlopen
    endpoint = config["base_url"].rstrip("/") + "/responses"
    logical_call_id = uuid.uuid4().hex
    log_filename = config.get("call_log_filename", "full_agent_calls.jsonl")
    for attempt in range(config["max_retries"] + 1):
        started = now()
        req = request.Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"}, method="POST")
        record = {"logical_call_id": logical_call_id, "attempt": attempt + 1, "retry_count": attempt,
                  "started_at": started, "endpoint": endpoint, "request": payload, **call_context}
        transient = False
        detail = ""
        retry_after = None
        try:
            with transport(req, timeout=config["request_timeout_seconds"]) as result:
                raw = result.read().decode("utf-8")
                record["http_status"] = result.status
                record["request_id"] = result.headers.get("x-request-id") or result.headers.get("x-dashscope-request-id")
            try:
                response = json.loads(raw)
            except ValueError:
                record.update(response_raw=raw, status="invalid_json", finished_at=now())
                append_log(root, scrub(record, secret), log_filename)
                raise GenerationError("API returned invalid JSON")
            record.update(response=response, finished_at=now())
            try:
                spoken = extract_text(response)
            except GenerationError as exc:
                record.update(status="invalid_output", error=str(exc))
                append_log(root, scrub(record, secret), log_filename)
                raise
            record.update(status="success", spoken_text=spoken)
            append_log(root, scrub(record, secret), log_filename)
            return spoken, logical_call_id, response
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            transient = exc.code == 429 or 500 <= exc.code <= 599
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            record.update(status="http_error", http_status=exc.code, error=detail, finished_at=now())
            try:
                structured_error = json.loads(detail)
                record["request_id"] = structured_error.get("request_id")
                record["error_code"] = structured_error.get("code")
            except (ValueError, AttributeError):
                pass
        except (error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            transient = True
            detail = str(exc)
            record.update(status="network_error", error=detail, finished_at=now())
        append_log(root, scrub(record, secret), log_filename)
        if not transient or attempt == config["max_retries"]:
            raise GenerationError(scrub("Request failed: " + detail[:800], secret))
        delay = min(30, 2 ** (attempt + 1))
        if retry_after:
            try:
                delay = min(60, max(delay, float(retry_after)))
            except ValueError:
                pass
        print("temporary API error; retry in %.1fs" % delay, flush=True)
        sleep(delay)


def mutual_closing(rows, minimum=8):
    if len(rows) < minimum * 2 or len(rows) % 2:
        return False
    client = rows[-2]["text"]
    counselor = rows[-1]["text"]
    closing = r"先到这里|先聊到这里|今天就到|下次见|下周见|下次再聊|下次再说|今天先这样|先这样吧|今天先聊到|下次接着|下次继续|先走了"
    client_ends = bool(re.search(closing, client))
    # Once the client explicitly ends, a brief acknowledgement need not repeat goodbye.
    counselor_ends = bool(re.search(closing, counselor)) or (client_ends and len(counselor) <= 140 and not re.search(r"[?？]", counselor))
    return client_ends and counselor_ends


def expression_issues(text, pair_number=None):
    """Surface checks only; clinical meaning remains the independent reviewer's task."""
    issues = []
    if re.search(r'["“”]', text):
        issues.append("quotation-heavy wording")
    if re.search(r"(说得|说法|这个词|这句话|你把这个).{0,8}(准|真实|重要|挺好|很清楚)|注意到.{0,8}(就够|就行)|不用.{0,5}(解释|归因)", text):
        issues.append("word rating or prematurely closing exploration")
    if pair_number is not None and pair_number < 8 and re.search(r"下次见|下周见|下次再聊|今天先到这里|今天先这样", text):
        issues.append("premature goodbye before substantive session minimum")
    return issues


def validate_transcript(data):
    issues = []
    rows = data["utterances"]
    if not 16 <= len(rows) <= 24 or len(rows) % 2:
        issues.append("Each completed session must have 8–12 full pairs")
    for index, row in enumerate(rows):
        if row["role"] != ROLES[index % 2] or not row["text"].strip():
            issues.append("Invalid role order or empty utterance at index %d" % index)
    if len({row["id"] for row in rows}) != len(rows):
        issues.append("Duplicate utterance IDs")
    return issues


def render_dialogue(root, data):
    path = root / "dialogues" / data["case_id"] / ("S%02d.md" % data["session_number"])
    label = "模型审阅通过，未进行人工审阅" if data.get("model_review_status") == "approved" else (
        "等待模型审阅，未进行人工审阅" if (root / "configs/review_policy.json").exists() else "人工审阅状态：待审阅")
    lines = ["# %s · S%02d" % (data["case_id"], data["session_number"]), "",
             "> 拟真合成咨询案例；非真实患者记录。" + label + "。", ""]
    for row in data["utterances"]:
        name = "来访者" if row["role"] == "client" else "咨询师"
        lines += ["**%s · %s**" % (row["id"], name), "", row["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def human_review_passes(case_reviews):
    values = []
    ids = []
    for reviewer in case_reviews.get("reviewers", []):
        if reviewer.get("kind") != "human" or not reviewer.get("reviewer_id"):
            return False
        ids.append(reviewer["reviewer_id"])
        if reviewer.get("major_issues") != []:
            return False
        for dimension in ("naturalness", "counseling_quality", "longitudinal_plausibility"):
            score = reviewer.get("scores", {}).get(dimension)
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not 1 <= score <= 5:
                return False
            values.append(score)
    return len(ids) == 2 and len(set(ids)) == 2 and sum(values) / len(values) >= 4


def require_s1_human_gate(root):
    scores = read_json(root / "audit" / "quality_scores.json")
    for case_id in CASE_IDS:
        review = next((item for item in scores["sessions"] if item["case_id"] == case_id and item["session_number"] == 1), {})
        if not human_review_passes(review):
            raise GenerationError("S1 human review gate not passed for " + case_id)
        data = read_json(root / "dialogues" / case_id / "S01.json")
        if validate_transcript(data) or data["continuity_review"]["status"] != "passed":
            raise GenerationError("S1 structural/continuity checks not passed for " + case_id)


def validate_model_review(root, case_id, number):
    path = root / "audit/agent_reviews" / case_id / ("S%02d.json" % number)
    if not path.exists():
        raise GenerationError("Model review not yet available for %s S%02d" % (case_id, number))
    review = read_json(path)
    data = read_json(root / "dialogues" / case_id / ("S%02d.json" % number))
    if validate_transcript(data) or data.get("status") not in COMPLETE_STATUSES:
        raise GenerationError("Model review cannot approve an incomplete or invalid transcript")
    accepted_reviewers = {("gpt-6.1-sol", "high")}
    policy_path = root / "configs/review_policy.json"
    if policy_path.exists():
        policy = read_json(policy_path)
        if (policy.get("mode") == "user_authorized_model_review" and policy.get("human_review_claim") is False
                and (policy.get("reviewer_model"), policy.get("reasoning_effort")) == ("gpt-6-luna", "low")):
            accepted_reviewers.add(("gpt-6-luna", "low"))
    if (review.get("kind") != "model_review" or (review.get("reviewer_model"), review.get("reasoning_effort")) not in accepted_reviewers
            or review.get("case_id") != case_id or review.get("session_number") != number
            or review.get("transcript_sha256") != digest(visible_utterances(data["utterances"]))):
        raise GenerationError("Missing requested reviewer identity or stale transcript hash")
    values = [review.get("scores", {}).get(k) for k in ("naturalness", "counseling_quality", "longitudinal_plausibility")]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not 1 <= v <= 5 for v in values):
        raise GenerationError("Invalid model review scores")
    threshold = 3 if review.get("reviewer_model") == "gpt-6-luna" else 4
    if review.get("decision") != "approve" or review.get("major_issues") != [] or sum(values)/3 < threshold:
        raise GenerationError("Model review requires revision for %s S%02d" % (case_id, number))
    return review


def require_s1_review_gate(root):
    policy_path = root / "configs/review_policy.json"
    if not policy_path.exists():
        return require_s1_human_gate(root)
    policy = read_json(policy_path)
    if policy.get("mode") != "user_authorized_model_review" or policy.get("human_review_claim") is not False:
        raise GenerationError("Invalid explicit review policy")
    for case_id in CASE_IDS:
        validate_model_review(root, case_id, 1)


def recover_logged_call(root, case_id, number, utterance_id, payload, config_hash):
    """Recover a successful HTTP call if the process died before transcript commit."""
    path = root / "generation_logs" / "full_agent_calls.jsonl"
    if not path.exists():
        return None
    matched = None
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        try:
            record = json.loads(line)
        except ValueError:
            if index == len(lines) - 1:
                continue
            raise GenerationError("Corrupt generation log at line %d" % (index + 1))
        if (record.get("case_id") == case_id and record.get("session_number") == number
                and record.get("utterance_id") == utterance_id and record.get("status") == "success"
                and record.get("config_hash") == config_hash and record.get("request") == payload):
            matched = record
    return matched


def generate_session(root, config, plan, number, prompts, secret):
    case_id = plan["case_id"]
    previous = public_history(root, case_id, number)
    ledger_path = root / "facts" / (case_id + ".json")
    ledger = read_json(ledger_path)
    path = root / "dialogues" / case_id / ("S%02d.json" % number)
    config_hash = digest({"generation": config, "case": plan, "prompts": prompts,
                          "previous": previous, "confirmed_additions": [fact for fact in ledger.get("confirmed_additions", [])
                                                                       if fact["first_session"] < number]})
    if path.exists():
        data = read_json(path)
        if data["status"] in COMPLETE_STATUSES:
            print(case_id + " S%02d already complete; skipped" % number, flush=True)
            return
        if data["config_hash"] != config_hash:
            raise GenerationError("Configuration changed; retain the old run and start an explicitly versioned revision")
    else:
        data = {"schema_version": "1.0", "synthetic": True, "case_id": case_id, "session_number": number,
                "relative_week": card_for(plan, number)["relative_week"], "status": "generating",
                "human_review_status": "pending", "continuity_review": {"status": "pending", "issues": [], "reviewer": None},
                "created_at": now(), "config_hash": config_hash, "prompt_version": config["prompt_version"],
                "generation_config_snapshot": config, "utterances": []}
        write_json(path, data)
    rows = data["utterances"]
    # Preserve a committed transcript prefix; do not resend completed turns.
    for index, row in enumerate(rows):
        if row["role"] != ROLES[index % 2] or not row["text"].strip():
            raise GenerationError("Invalid saved transcript prefix")
    try:
        while len(rows) < config["max_pairs"] * 2:
            if mutual_closing(rows, config["min_pairs"]):
                break
            role = ROLES[len(rows) % 2]
            utterance_id = "%s-S%02d-U%02d" % (case_id, number, len(rows) + 1)
            context = context_for(role, plan, number, previous, rows, ledger)
            payload = build_payload(role, prompts[role], config, context)
            recovered = recover_logged_call(root, case_id, number, utterance_id, payload, config_hash)
            if recovered:
                spoken, call_id, response = recovered["spoken_text"], recovered["logical_call_id"], recovered["response"]
            else:
                spoken, call_id, response = call_api(root, config, payload,
                    {"case_id": case_id, "session_number": number, "role": role, "utterance_id": utterance_id,
                     "prompt_version": config["prompt_version"], "config_hash": config_hash}, secret)
            if role == "counselor" and config.get("expression_gate"):
                pair_number = (len(rows)+1)//2
                for expression_revision in range(1, 3):
                    if not expression_issues(spoken, pair_number):
                        break
                    # Candidate was never sent to the client. Keep its raw call; request one new utterance.
                    repair = dict(payload)
                    if config.get("candidate_editing_instruction"):
                        repair["input"] = [{"role": "system", "content": config["candidate_editing_instruction"]}, payload["input"][1]]
                    else:
                        repair["input"] = list(payload["input"])
                    repair["input"] += [{"role": "user", "content":
                        "现在重新生成一条咨询师发言。上一条是未发送的候选，因表达检查未通过而弃用：\n" + spoken +
                        "\n请直接回应同一个来访者最新发言，用1–3句具体口语。不得使用任何双引号，不评价词句，不解析比喻。"
                        "可以问一个能获取具体经历或辨别心理线索的简短问题，也可以只作贴合当前处境的回应。"
                        "让来访者继续探索，不以注意到就够或不用解释来打断。心理解释应暂定，不能添加未证实的结论。只输出实际发言。"}]
                    repaired = recover_logged_call(root, case_id, number, utterance_id, repair, config_hash)
                    if repaired:
                        spoken, call_id, response = repaired["spoken_text"], repaired["logical_call_id"], repaired["response"]
                    else:
                        spoken, call_id, response = call_api(root, config, repair,
                            {"case_id": case_id, "session_number": number, "role": role, "utterance_id": utterance_id,
                             "candidate_revision": expression_revision, "candidate_not_yet_sent_to_client": True,
                             "prompt_version": config["prompt_version"], "config_hash": config_hash}, secret)
                if expression_issues(spoken, pair_number):
                    raise GenerationError("Counselor expression gate still failed after two candidate revisions")
            rows.append({"id": utterance_id, "role": role, "text": spoken, "call_id": call_id,
                         "response_id": response.get("id"), "model": response.get("model", config["model"]), "created_at": now()})
            data.update(status="generating", updated_at=now())
            write_json(path, data)
            render_dialogue(root, data)
            print("%s S%02d pair %02d %s saved" % (case_id, number, (len(rows) + 1) // 2, role), flush=True)
        issues = validate_transcript(data)
        if issues:
            raise GenerationError("; ".join(issues))
        model_policy = (root / "configs/review_policy.json").exists()
        data.update(status="awaiting_model_review" if model_policy else "awaiting_human_review",
                    human_review_status="not_performed" if model_policy else "pending",
                    ending_reason="mutual_closing" if mutual_closing(rows, config["min_pairs"]) else "budget_limit",
                    completed_at=now(), structural_checks={"status": "passed", "issues": issues, "pairs": len(rows) // 2})
        data.pop("last_error", None)
        write_json(path, data)
        render_dialogue(root, data)
        # New facts are candidates until a separate evidence review accepts them.
        ledger["pending_dialogue_reviews"] = sorted(set(ledger.get("pending_dialogue_reviews", []) + [number]))
        write_json(ledger_path, ledger)
        print("%s S%02d completed; %s review pending" % (case_id, number, "model" if model_policy else "human"), flush=True)
    except GenerationError as exc:
        data.update(status="interrupted", last_error=str(exc), updated_at=now())
        write_json(path, data)
        raise


def run_checks(root):
    report = {"checked_at": now(), "sessions": [], "request_checks": [], "issues": [], "requests_checked": 0,
              "successful_calls": 0, "failed_calls": 0}
    config = read_json(root / "configs" / "generation_config.json")
    for case_id in CASE_IDS:
        plan = read_json(root / "configs" / "case_plans" / (case_id + ".json"))
        if [card["session_number"] for card in plan["session_cards"]] != list(range(1, 9)):
            report["issues"].append(case_id + " does not have 8 ordered cards")
        if not (root / "dialogues" / case_id / "S01.json").exists():
            report["issues"].append(case_id + " S1 not generated")
        for path in sorted((root / "dialogues" / case_id).glob("S*.json")):
            data = read_json(path)
            issues = validate_transcript(data)
            if data["status"] not in COMPLETE_STATUSES:
                issues.append("Session is not complete")
            report["sessions"].append({"case_id": case_id, "session": data["session_number"], "pairs": len(data["utterances"]) // 2, "issues": issues})
            report["issues"].extend(issues)
    log_path = root / "generation_logs" / "full_agent_calls.jsonl"
    if log_path.exists():
        successful_ids = set()
        for line in log_path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            report["requests_checked"] += 1
            if entry["status"] == "success":
                successful_ids.add(entry["logical_call_id"])
                report["successful_calls"] += 1
            else:
                report["failed_calls"] += 1
            payload = entry["request"]
            context = json.loads(payload["input"][1]["content"])
            issues = []
            if payload.get("store") is not False or any(key in payload for key in ("conversation", "previous_response_id", "tools")):
                issues.append("Unexpected shared state or tools")
            if entry.get("role") not in ROLES:
                # Connectivity probes and frozen checkpoint answers have separate input audits.
                if issues:
                    report["issues"].extend(issues)
                continue
            if entry["role"] == "counselor" and set(context) - {"session", "previous_session_dialogues", "current_session_dialogue", "time_notice"}:
                issues.append("Counselor received private context fields")
            if entry["role"] == "client":
                if set(context) - {"session", "previous_session_dialogues", "current_session_dialogue", "time_notice", "client_profile", "private_life_history", "occurred_life_events", "current_session_card", "confirmed_additional_facts", "turn_instruction"}:
                    issues.append("Client received research fields")
                if any(item["session_number"] >= entry["session_number"] for item in context["occurred_life_events"]):
                    issues.append("Client received a future event")
            if any(item["session_number"] >= entry["session_number"] for item in context["previous_session_dialogues"]):
                issues.append("Visible history contains current or future session")
            if issues:
                report["request_checks"].append({"utterance_id": entry["utterance_id"], "issues": issues})
                report["issues"].extend(issues)
        for case_id in CASE_IDS:
            for path in (root / "dialogues" / case_id).glob("S*.json"):
                for row in read_json(path)["utterances"]:
                    if row["call_id"] not in successful_ids:
                        report["issues"].append("Transcript lacks logged successful call: " + row["id"])
    report["status"] = "passed" if not report["issues"] else "failed"
    write_json(root / "audit" / "structural_checks.json", report)
    return report


def preflight(root):
    """Build all 48 role contexts without API calls or simulated dialogues."""
    report = {"checked_at": now(), "case_count": 0, "card_count": 0, "role_contexts_checked": 0, "issues": []}
    config = read_json(root / "configs/generation_config.json")
    if not 8 <= config["min_pairs"] <= config["target_pairs"] <= config["max_pairs"] <= 12:
        report["issues"].append("Invalid pair limits")
    if config["max_retries"] != 2 or config["store"] is not False or config["stream"] is not False:
        report["issues"].append("Configuration differs from the approved retry/state/stream policy")
    prompts = {role: (root / "configs" / (role + "_system_prompt.txt")).read_text(encoding="utf-8") for role in ROLES}
    forbidden = {"trajectory", "research_plan", "GOLD_INTERPRETATION", "EXPECTED_RESPONSE", "LATENT_TRAJECTORY", "session_cards"}
    for case_id in CASE_IDS:
        plan = read_json(root / "configs/case_plans" / (case_id + ".json"))
        report["case_count"] += 1
        if [item["session_number"] for item in plan["session_cards"]] != list(range(1, 9)):
            report["issues"].append(case_id + " has invalid cards")
        ledger = read_json(root / "facts" / (case_id + ".json"))
        if ledger["stable_profile"] != plan["client_profile"]:
            report["issues"].append(case_id + " ledger profile differs from case profile")
        for number in range(1, 9):
            report["card_count"] += 1
            placeholder = [{"id": "INPUT_CONSTRUCTION_ONLY", "role": "client", "text": "[输入结构检查占位，不是咨询对话]"}]
            for role in ROLES:
                context = context_for(role, plan, number, [], [] if role == "client" else placeholder, ledger)
                if set(context) & forbidden:
                    report["issues"].append("Research keys exposed to " + role)
                if role == "counselor" and set(context) - {"session", "previous_session_dialogues", "current_session_dialogue", "time_notice"}:
                    report["issues"].append("Counselor private context")
                if role == "client" and len(context["occurred_life_events"]) != number - 1:
                    report["issues"].append("Incorrect past-event boundary")
                payload = build_payload(role, prompts[role], config, context)
                if payload["store"] is not False or payload["stream"] is not False:
                    report["issues"].append("Unexpected request state")
                report["role_contexts_checked"] += 1
    report["status"] = "passed" if not report["issues"] else "failed"
    write_json(root / "audit/preflight_checks.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("s1", "session", "check", "preflight"), default="s1")
    parser.add_argument("--case", choices=CASE_IDS)
    parser.add_argument("--session", type=int)
    args = parser.parse_args(argv)
    if args.phase == "preflight":
        report = preflight(ROOT)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "passed" else 1
    if args.phase == "check":
        report = run_checks(ROOT)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "passed" else 1
    config = read_json(ROOT / "configs" / "generation_config.json")
    report = preflight(ROOT)
    if report["status"] != "passed":
        raise GenerationError("Preflight failed: " + "; ".join(report["issues"]))
    secret = os.environ.get(config["api_key_env"], "")
    if not secret:
        raise GenerationError("Missing process environment variable: " + config["api_key_env"])
    prompts = {role: (ROOT / "configs" / (role + "_system_prompt.txt")).read_text(encoding="utf-8") for role in ROLES}
    if args.phase == "session":
        if args.case is None or args.session is None or not 1 <= args.session <= 8:
            parser.error("--phase session requires --case and --session 1..8")
        if args.session > 1:
            require_s1_review_gate(ROOT)
        jobs = [(args.case, args.session)]
    else:
        jobs = [(case_id, 1) for case_id in CASE_IDS]
    with exclusive_run(ROOT):
        meta_path = ROOT / "generation_logs" / "generation_metadata.json"
        meta = read_json(meta_path) if meta_path.exists() else {"runs": []}
        run = {"run_id": uuid.uuid4().hex, "started_at": now(), "phase": args.phase, "model_requested": config["model"],
               "prompt_version": config["prompt_version"], "python_version": sys.version.split()[0],
               "credential_source": "process environment: " + config["api_key_env"], "status": "running"}
        meta["runs"].append(run)
        write_json(meta_path, meta)
        try:
            for case_id, number in jobs:
                plan = read_json(ROOT / "configs" / "case_plans" / (case_id + ".json"))
                generate_session(ROOT, config, plan, number, prompts, secret)
            run.update(status="completed_awaiting_human_review", finished_at=now())
        except GenerationError as exc:
            run.update(status="failed", finished_at=now(), error=str(exc))
            raise
        finally:
            write_json(meta_path, meta)
    print("P0 generation finished. S2–S8 remain behind the S1 human-review gate.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GenerationError as failure:
        print("STOP: " + str(failure), file=sys.stderr, flush=True)
        sys.exit(1)
