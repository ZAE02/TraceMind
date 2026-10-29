"""Check the requested DashScope Responses model IDs without persisting credentials."""
import json
import sys
from urllib import request, error

secret = sys.stdin.readline().strip()
if not secret:
    raise SystemExit("Missing API key on stdin")
for model in ("qwen3.7-flash-2026-07-15", "qwen3.8-max"):
    payload = {"model": model, "input": [{"role": "user", "content": "只回复：连接成功"}],
               "store": False, "stream": False, "reasoning": {"effort": "none"},
               "max_output_tokens": 80}
    req = request.Request("https://dashscope.aliyuncs.com/compatible-mode/v1/responses",
                          json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          {"Authorization": "Bearer " + secret, "Content-Type": "application/json"},
                          method="POST")
    try:
        with request.urlopen(req, timeout=90) as response:
            body = json.load(response)
        print(json.dumps({"model": model, "status": body.get("status"),
                          "returned_model": body.get("model"), "usage": body.get("usage")},
                         ensure_ascii=False), flush=True)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").replace(secret, "[REDACTED]")
        print(json.dumps({"model": model, "http_status": exc.code, "detail": detail[:500]},
                         ensure_ascii=False), flush=True)
