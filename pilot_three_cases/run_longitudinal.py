"""Concurrent case generation with sequential per-case GPT reviewer gates."""
import json
import os
import time
import uuid
import re
import threading
from concurrent.futures import ThreadPoolExecutor
import generator as g

ROOT = g.ROOT
STATE_LOCK = threading.RLock()
STATE = {"started_at": g.now(), "status": "running", "cases": {}}


def state(case_id, number, status, **extra):
    with STATE_LOCK:
        STATE["cases"][case_id] = {"session": number, "status": status, "updated_at": g.now(), **extra}
        g.write_json(ROOT / "generation_logs/batch_state.json", STATE)
    print("%s S%02d %s" % (case_id, number, status), flush=True)


def accept_review(case_id, number):
    review = g.validate_model_review(ROOT, case_id, number)
    path = ROOT / "dialogues" / case_id / ("S%02d.json" % number)
    data = g.read_json(path)
    valid = {row["id"] for row in data["utterances"]}
    ledger_path = ROOT / "facts" / (case_id + ".json")
    ledger = g.read_json(ledger_path)
    existing = {fact["fact"] for fact in ledger.get("confirmed_additions", [])}
    for fact in review.get("new_facts", []):
        if (not isinstance(fact, dict) or not fact.get("fact") or not fact.get("evidence")
                or not set(fact["evidence"]) <= valid or fact.get("first_session") != number):
            raise g.GenerationError("Invalid evidence for reviewer new fact")
        if fact["fact"] in existing:
            continue
        ledger.setdefault("confirmed_additions", []).append({**fact,
            "id": "%s-F%03d" % (case_id, len(ledger.get("confirmed_additions", [])) + 1),
            "reviewed_by": review["reviewer_model"] + "/" + review["reasoning_effort"] + " model review", "reviewed_at": g.now(),
            "consistency_check": "Reviewed against actual dialogue and prior facts; bounded synthetic report, not clinical truth."})
        existing.add(fact["fact"])
    ledger["pending_dialogue_reviews"] = [n for n in ledger.get("pending_dialogue_reviews", []) if n != number]
    if number == 1 and case_id == "C02":
        ledger["reviewed_candidates"] = ledger.get("pending_new_fact_candidates", [])
        ledger["pending_new_fact_candidates"] = []
        ledger["date_resolution"] = "GPT independent reviewer: the previous Saturday episode is an additional event, not the undisclosed Thursday episode. Preserve both dates."
    g.write_json(ledger_path, ledger)
    data.update(status="reviewed_by_model", human_review_status="not_performed", model_review_status="approved",
        model_review_reference="audit/agent_reviews/%s/S%02d.json" % (case_id, number),
        continuity_review={"status": "passed", "reviewer": review["reviewer_model"] + "/" + review["reasoning_effort"], "kind": "model_review",
            "reviewed_at": g.now(), "issues": [], "minor_issues": review.get("minor_issues", []),
            "transcript_sha256": review["transcript_sha256"]})
    g.write_json(path, data)
    g.render_dialogue(ROOT, data)
    return review


def archive_revision(case_id, number, revision, keep_prefix=0):
    path = ROOT / "dialogues" / case_id / ("S%02d.json" % number)
    data = g.read_json(path)
    destination = ROOT / "revisions" / case_id / ("S%02d" % number) / ("r%d" % revision)
    if destination.exists():
        destination = destination.with_name(destination.name + "-" + uuid.uuid4().hex[:8])
    destination.mkdir(parents=True, exist_ok=True)
    g.write_json(destination / path.name, data)
    ledger_path = ROOT / "facts" / (case_id + ".json")
    ledger = g.read_json(ledger_path)
    g.write_json(destination / "facts_before_revision.json", ledger)
    ledger["confirmed_additions"] = [f for f in ledger.get("confirmed_additions", []) if f["first_session"] != number]
    for key in ("reported_intentions", "pending_new_fact_candidates"):
        ledger[key] = [f for f in ledger.get(key, []) if f.get("first_session") != number]
    g.write_json(ledger_path, ledger)
    md = path.with_suffix(".md")
    if md.exists():
        (destination / md.name).write_text(md.read_text(encoding="utf-8"), encoding="utf-8")
    review_path = ROOT / "audit/agent_reviews" / case_id / ("S%02d.json" % number)
    if review_path.exists():
        g.write_json(destination / "review.json", g.read_json(review_path))
        review_path.unlink()
    marker = ROOT / "generation_logs/review_ready" / ("%s_S%02d.json" % (case_id, number))
    if marker.exists():
        marker.unlink()
    return data["utterances"][:keep_prefix]


def generate_revision(case_id, number, revision, prefix, config, prompts, secret):
    active = dict(config, generation_revision="%s-S%02d-r%d" % (case_id, number, revision))
    plan = g.read_json(ROOT / "configs/case_plans" / (case_id + ".json"))
    if prefix:
        history = g.public_history(ROOT, case_id, number)
        ledger = g.read_json(ROOT / "facts" / (case_id + ".json"))
        fingerprint = g.digest({"generation": active, "case": plan, "prompts": prompts, "previous": history,
            "confirmed_additions": [f for f in ledger.get("confirmed_additions", []) if f["first_session"] < number]})
        g.write_json(ROOT / "dialogues" / case_id / ("S%02d.json" % number), {
            "schema_version": "1.0", "synthetic": True, "case_id": case_id, "session_number": number,
            "relative_week": number, "status": "generating", "human_review_status": "not_performed",
            "continuity_review": {"status": "pending", "issues": [], "reviewer": None},
            "created_at": g.now(), "config_hash": fingerprint, "prompt_version": config["prompt_version"],
            "generation_config_snapshot": active, "revision": revision, "preserved_prefix_length": len(prefix), "utterances": prefix})
    else:
        path = ROOT / "dialogues" / case_id / ("S%02d.json" % number)
        if path.exists():
            path.unlink()  # Exact file already copied to an immutable revision directory.
    g.generate_session(ROOT, active, plan, number, prompts, secret)


def wait_review(case_id, number):
    path = ROOT / "audit/agent_reviews" / case_id / ("S%02d.json" % number)
    marker = ROOT / "generation_logs/review_ready" / ("%s_S%02d.json" % (case_id, number))
    data = g.read_json(ROOT / "dialogues" / case_id / ("S%02d.json" % number))
    fingerprint = g.digest(g.visible_utterances(data["utterances"]))
    g.write_json(marker, {"case_id": case_id, "session_number": number, "transcript_sha256": fingerprint, "ready_at": g.now()})
    state(case_id, number, "awaiting_gpt_review")
    while True:
        if path.exists():
            try:
                review = g.read_json(path)
                if review.get("transcript_sha256") == fingerprint:
                    return review
            except (OSError, ValueError):
                pass
        time.sleep(5)


def run_case(case_id, config, prompts, secret):
    try:
        for number in range(2, 9):
            state(case_id, number, "generating")
            plan = g.read_json(ROOT / "configs/case_plans" / (case_id + ".json"))
            path = ROOT / "dialogues" / case_id / ("S%02d.json" % number)
            active = config
            if path.exists():
                saved = g.read_json(path)
                active = saved.get("generation_config_snapshot", config)
            g.generate_session(ROOT, active, plan, number, prompts, secret)
            for revision in range(3):
                review = wait_review(case_id, number)
                if review.get("decision") == "approve":
                    accept_review(case_id, number)
                    state(case_id, number, "approved_by_gpt")
                    break
                state(case_id, number, "revising", review=review.get("notes", ""))
                if revision == 2:
                    raise g.GenerationError("Two revisions exhausted; inspect reviewer findings before continuing")
                archive_revision(case_id, number, revision)
                generate_revision(case_id, number, revision+1, [], config, prompts, secret)
            else:
                raise g.GenerationError("No valid review")
        state(case_id, 8, "case_complete")
    except Exception as exc:
        state(case_id, number, "stopped", error=g.scrub(str(exc), secret))
        raise


def main():
    config = g.read_json(ROOT / "configs/generation_config.json")
    prompts = {role:(ROOT / "configs" / (role + "_system_prompt.txt")).read_text(encoding="utf-8") for role in g.ROLES}
    secret = os.environ.get(config["api_key_env"], "")
    if not secret:
        raise g.GenerationError("Missing credential environment")
    with g.exclusive_run(ROOT):
        metadata_path = ROOT / "generation_logs/generation_metadata.json"
        metadata = g.read_json(metadata_path)
        policy = g.read_json(ROOT / "configs/review_policy.json")
        run = {"run_id": uuid.uuid4().hex, "started_at": g.now(), "phase": "full_longitudinal",
               "generation_config": config, "review_model": policy["reviewer_model"], "review_reasoning_effort": policy["reasoning_effort"],
               "concurrent_cases": 3, "human_review_performed": False, "status": "running"}
        metadata["runs"].append(run)
        g.write_json(metadata_path, metadata)
        # Targeted intervention: discard the old stylistic prefix after documented rejection.
        c03 = g.read_json(ROOT / "dialogues/C03/S01.json")
        r03 = g.read_json(ROOT / "audit/agent_reviews/C03/S01.json")
        while r03["decision"] == "revise":
            revision = c03.get("revision", 0)
            if revision >= 3:
                raise g.GenerationError("C03 S1 fresh-generation intervention failed; inspect before continuing")
            prefix = archive_revision("C03", 1, revision, keep_prefix=0)
            generate_revision("C03", 1, revision+1, prefix, config, prompts, secret)
            r03 = wait_review("C03", 1)
            c03 = g.read_json(ROOT / "dialogues/C03/S01.json")
        for case_id in g.CASE_IDS:
            accept_review(case_id, 1)
        g.require_s1_review_gate(ROOT)
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(run_case, c, config, prompts, secret) for c in g.CASE_IDS]
            for future in futures:
                future.result()
        STATE.update(status="all_cases_complete", finished_at=g.now())
        g.write_json(ROOT / "generation_logs/batch_state.json", STATE)
        run.update(status="completed_model_review", finished_at=g.now())
        g.write_json(metadata_path, metadata)
    return 0


if __name__ == "__main__":
    import sys
    try:
        sys.exit(main())
    except Exception as exc:
        print("STOP: " + g.scrub(str(exc), os.environ.get("DASHSCOPE_API_KEY", "")), flush=True)
        sys.exit(1)
