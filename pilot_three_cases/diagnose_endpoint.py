"""Minimal same-provider/same-region endpoint diagnostic; no case content."""
import json
import os
import sys
from urllib import request, error
import generator as g


def main():
    secret = os.environ.get("DASHSCOPE_API_KEY", "")
    if not secret:
        raise g.GenerationError("DASHSCOPE_API_KEY is not configured")
    endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/responses"
    report = {"started_at": g.now(), "endpoint": endpoint,
              "purpose": "Diagnose access via documented same-region provider endpoint; no consultation content.",
              "documentation": "https://help.aliyun.com/zh/model-studio/regions", "attempts": []}
    for model in ("qwen3.8-27b", "deepseek-v4-flash"):
        payload = {"model": model, "input": "连通性测试：请只回复OK。", "store": False, "stream": False,
                   "reasoning": {"effort": "none"}, "max_output_tokens": 128}
        record = {"model": model, "request": payload, "started_at": g.now()}
        report["attempts"].append(record)
        req = request.Request(endpoint, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              {"Authorization": "Bearer " + secret, "Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
                record.update(http_status=response.status, response=g.scrub(result, secret), status="success", finished_at=g.now())
            report.update(status="provider_endpoint_accessible", model_accessible=model, finished_at=g.now())
            g.write_json(g.ROOT / "audit/endpoint_diagnostic.json", report)
            print(json.dumps({"endpoint": endpoint, "http_status": record["http_status"], "model": model,
                              "status": report["status"]}, ensure_ascii=False), flush=True)
            return 0
        except error.HTTPError as exc:
            body = g.scrub(exc.read().decode("utf-8", errors="replace"), secret)
            record.update(status="http_error", http_status=exc.code, error=body, finished_at=g.now())
        except (error.URLError, TimeoutError, OSError) as exc:
            record.update(status="network_error", error=g.scrub(str(exc), secret), finished_at=g.now())
        g.write_json(g.ROOT / "audit/endpoint_diagnostic.json", report)
        print(json.dumps({"model": model, "http_status": record.get("http_status"), "error": record["error"]}, ensure_ascii=False), flush=True)
    report.update(status="both_diagnostic_calls_failed", finished_at=g.now())
    g.write_json(g.ROOT / "audit/endpoint_diagnostic.json", report)
    return 1


if __name__ == "__main__":
    sys.exit(main())
