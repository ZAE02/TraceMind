"""Run the user-authorized Qwen retry, then DeepSeek if no utterance was generated."""
import json
import os
import sys
import uuid
import generator as g


def change_model(model, reason):
    config_path = g.ROOT / "configs/generation_config.json"
    config = g.read_json(config_path)
    if config["model"] == model:
        return
    existing = list((g.ROOT / "dialogues").glob("*/S*.json"))
    if any(g.read_json(path)["utterances"] for path in existing):
        raise g.GenerationError("Model switch stopped: preserve existing real utterances and review a separate run before switching")
    revision_id = uuid.uuid4().hex[:12]
    backup = g.ROOT / "configs/revisions" / ("model-switch-" + revision_id)
    g.write_json(backup / "generation_config.json", config)
    archive = g.ROOT / "generation_logs/interrupted_attempts" / revision_id
    archive.mkdir(parents=True, exist_ok=True)
    for path in existing:
        if g.ROOT.resolve() not in path.resolve().parents:
            raise g.GenerationError("Unexpected archive path")
        path.rename(archive / (path.parent.name + "_" + path.name))
    previous_model = config["model"]
    config["model"] = model
    g.write_json(config_path, config)
    revisions_path = g.ROOT / "audit/configuration_revisions.json"
    revisions = g.read_json(revisions_path)
    revisions["revisions"].append({
        "timestamp": g.now(), "type": "user_authorized_model_switch", "from_model": previous_model,
        "to_model": model, "reason": reason, "revision_id": revision_id,
        "successful_utterances_before_revision": 0,
        "original_configs": str(backup.relative_to(g.ROOT)),
        "original_empty_attempts": str(archive.relative_to(g.ROOT)),
    })
    g.write_json(revisions_path, revisions)


def main():
    report = {"started_at": g.now(), "authorization": "User requested retry of Qwen, then deepseek-v4-flash if unsuccessful.",
              "attempts": [], "status": "running"}
    report_path = g.ROOT / "audit/api_retry_report.json"
    for model in ("qwen3.8-27b", "deepseek-v4-flash"):
        change_model(model, "User-authorized retry/fallback; endpoint unchanged.")
        attempt = {"model": model, "started_at": g.now()}
        report["attempts"].append(attempt)
        g.write_json(report_path, report)
        print("Attempting " + model, flush=True)
        try:
            g.main(["--phase", "s1"])
        except g.GenerationError as exc:
            message = g.scrub(str(exc), os.environ.get("DASHSCOPE_API_KEY", ""))
            attempt.update(status="failed", error=message, finished_at=g.now())
            print("STOP " + model + ": " + message, flush=True)
            log = g.ROOT / "generation_logs/full_agent_calls.jsonl"
            if log.exists():
                last = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
                attempt["last_api_result"] = {key: last.get(key) for key in
                    ("http_status", "error_code", "request_id", "status", "retry_count")}
            g.write_json(report_path, report)
            if any(g.read_json(path)["utterances"] for path in (g.ROOT / "dialogues").glob("*/S*.json")):
                report.update(status="stopped_to_preserve_partial_dialogues", finished_at=g.now())
                g.write_json(report_path, report)
                return 1
        else:
            attempt.update(status="completed_three_S1", finished_at=g.now())
            report.update(status="completed_awaiting_human_review", model_used=model, finished_at=g.now())
            g.write_json(report_path, report)
            return 0
    report.update(status="both_models_failed", finished_at=g.now())
    g.write_json(report_path, report)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except g.GenerationError as exc:
        print("STOP: " + str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
