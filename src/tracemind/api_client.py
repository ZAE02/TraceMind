"""Bounded concurrent transport with atomic durable rate limits and private credentials."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

from .config import LOCAL, ROOT

SKILL = ROOT / ".agents/skills/tracemind-api/SKILL.md"


def account_service_notice(text):
    # "account of the verification process" is ordinary prose, not an account notice.
    return re.search(
        r"account.{0,25}(?:verification\s+(?:is\s+)?(?:required|needed)|"
        r"(?:requires?|needs?)\s+(?:identity\s+)?verif)|verify\s+your\s+account|"
        r"verification.{0,20}required|service.{0,20}disabled|terms of service|"
        r"违反.{0,8}条款|账户.{0,8}验证|账号.{0,8}验证|服务.{0,8}禁用",
        text,
        re.I,
    )


class APIStopped(RuntimeError):
    """An actionable, already sanitized stop reason."""


def read_policy(path=SKILL):
    raw = path.read_text(encoding="utf-8")
    policy = json.loads(raw.split("<!-- API_POLICY_JSON", 1)[1].split("API_POLICY_JSON -->", 1)[0])
    if not (
        1 <= policy["concurrency"] <= 4
        and policy["interval_seconds"] >= 10
        and (
            policy["daily_limit"] is None
            or isinstance(policy["daily_limit"], int)
            and policy["daily_limit"] >= 1
        )
        and (
            policy["batch_size"] is None
            or isinstance(policy["batch_size"], int)
            and policy["batch_size"] >= 1
        )
        and 0 <= policy["max_retries"] <= 2
        and policy["retry_429_seconds"] >= 300
        and policy["backoff_seconds"] >= 60
    ):
        raise APIStopped("API skill policy is invalid or weaker than authorized limits")
    return policy, hashlib.sha256(raw.encode()).hexdigest()


@contextmanager
def single_flight(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise APIStopped("another API process owns the global single-flight lock") from None
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise APIStopped("HTTP redirect refused; inspect configured endpoint")


class LimitedAPI:
    def __init__(self, state=LOCAL / "api", config_path=LOCAL / "api-config.json"):
        config = (
            json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
        )
        self.base = os.environ.get("TRACEMIND_API_BASE_URL", config.get("base_url", "")).rstrip("/")
        self.key = os.environ.get("TRACEMIND_API_KEY", config.get("api_key", ""))
        self.model = os.environ.get("TRACEMIND_API_MODEL", config.get("model", ""))
        parsed = urlsplit(self.base)
        if (
            not self.key
            or self.model != "gemini-3.8-flash"
            or parsed.username
            or parsed.query
            or parsed.fragment
            or parsed.scheme not in {"http", "https"}
        ):
            raise APIStopped("missing/invalid local API configuration or unauthorized model")
        self.state = Path(state)
        self.state.mkdir(parents=True, exist_ok=True)
        self.database = self.state / "requests.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS attempts (
                  id TEXT PRIMARY KEY, task TEXT NOT NULL, started REAL NOT NULL,
                  day TEXT NOT NULL, skill_sha TEXT NOT NULL, status INTEGER,
                  outcome TEXT, input_sha TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY CHECK(id=1),
                  blocked TEXT, pause_until REAL NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO control(id) VALUES(1);
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
            if "finished" not in columns:
                conn.execute("ALTER TABLE attempts ADD COLUMN finished REAL")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def connect(self):
        return sqlite3.connect(self.database, timeout=5)

    def _finish(self, attempt_id, status, outcome, *, blocked=None, pause_until=0):
        with self.connect() as conn:
            conn.execute(
                "UPDATE attempts SET status=?,outcome=?,finished=coalesce(finished,?) WHERE id=?",
                (status, outcome, time.time(), attempt_id),
            )
            conn.execute(
                "UPDATE control SET blocked=coalesce(?,blocked),pause_until=max(pause_until,?) WHERE id=1",
                (blocked, pause_until),
            )

    def _reserve(self, task, body, policy, skill_sha):
        while True:
            policy, skill_sha = read_policy()
            if (self.state / "STOP").exists():
                raise APIStopped("request paused by local STOP marker")
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                blocked, pause_until = conn.execute(
                    "SELECT blocked,pause_until FROM control WHERE id=1"
                ).fetchone()
                if blocked:
                    raise APIStopped("API task is blocked: " + blocked)
                used = conn.execute(
                    "SELECT count(*) FROM attempts WHERE task=?", (task,)
                ).fetchone()[0]
                if used >= policy["max_retries"] + 1:
                    raise APIStopped("task attempt limit reached; no further retry")
                last = conn.execute("SELECT max(started) FROM attempts").fetchone()[0]
                now = time.time()
                deadline = max(pause_until, last + policy["interval_seconds"] + 1 if last else 0)
                if now >= deadline:
                    day = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).date().isoformat()
                    if (
                        policy["daily_limit"] is not None
                        and conn.execute(
                            "SELECT count(*) FROM attempts WHERE day=?", (day,)
                        ).fetchone()[0]
                        >= policy["daily_limit"]
                    ):
                        raise APIStopped(
                            "daily API budget reached (including discovery and retries)"
                        )
                    attempt_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO attempts(id,task,started,day,skill_sha,input_sha) VALUES(?,?,?,?,?,?)",
                        (
                            attempt_id,
                            task,
                            now,
                            day,
                            skill_sha,
                            hashlib.sha256(body or b"").hexdigest(),
                        ),
                    )
                    return attempt_id, used
            # Do not hold a SQLite write transaction while another request runs/waits.
            time.sleep(min(1, max(0.001, deadline - time.time())))

    @contextmanager
    def _request_slot(self, task):
        # Task lock prevents parallel retries of the same task. OS releases slots on exit/crash.
        task_key = hashlib.sha256(task.encode()).hexdigest()
        with single_flight(self.state / "task_locks" / (task_key + ".lock")):
            while True:
                policy, _ = read_policy()
                if (self.state / "STOP").exists():
                    raise APIStopped("request paused by local STOP marker")
                with self.connect() as conn:
                    blocked = conn.execute("SELECT blocked FROM control WHERE id=1").fetchone()[0]
                if blocked:
                    raise APIStopped("API task is blocked: " + blocked)
                acquired = None
                for index in range(policy["concurrency"]):
                    candidate = single_flight(self.state / "slots" / f"{index}.lock")
                    try:
                        candidate.__enter__()
                    except APIStopped:
                        continue
                    acquired = candidate
                    break
                if acquired is not None:
                    try:
                        yield
                    finally:
                        acquired.__exit__(None, None, None)
                    return
                time.sleep(0.1)

    def request(self, method, route, task, payload=None):
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        with self._request_slot(task):
            while True:
                policy, skill_sha = read_policy()
                attempt_id, used = self._reserve(task, body, policy, skill_sha)
                request = Request(
                    self.base + route,
                    data=body,
                    method=method,
                    headers={
                        "Authorization": "Bearer " + self.key,
                        "Content-Type": "application/json",
                    },
                )
                status, headers = 0, {}
                try:
                    with self.opener.open(request, timeout=120) as response:
                        status, headers = response.status, response.headers
                        raw = response.read(2_000_001)
                    if len(raw) > 2_000_000:
                        raise APIStopped("response exceeded configured size limit")
                except HTTPError as exc:
                    status, headers = exc.code, exc.headers
                    raw = exc.read(16000)
                except (TimeoutError, URLError):
                    raw = b""
                except Exception:
                    self._finish(
                        attempt_id, status, "transport stopped", blocked="inspect transport error"
                    )
                    raise APIStopped("transport stopped; inspect endpoint configuration") from None
                text = raw.decode("utf-8", errors="replace")
                # Preserve original HTTP200 JSON before a notice stop, but never credentials.
                # It remains a blocked attempt, not a successful reusable completion.
                if status == 200:
                    try:
                        inspectable = json.loads(text)
                    except ValueError:
                        inspectable = None
                    if self.key in text or self.key in json.dumps(inspectable, ensure_ascii=False):
                        self._finish(
                            attempt_id,
                            status,
                            "credential reflected",
                            blocked="credential reflected by provider",
                        )
                        raise APIStopped("provider reflected a credential; response was not saved")
                    if inspectable is not None:
                        from .benchmark.builder import write

                        write(self.state / "responses" / (attempt_id + ".json"), inspectable)
                fatal_notice = account_service_notice(text)
                if status in (401, 403) or fatal_notice:
                    reason = f"HTTP {status}: credential/account/service stop; no retries"
                    self._finish(attempt_id, status, reason, blocked=reason)
                    raise APIStopped(reason)
                if status == 429 or status == 0 or status >= 500:
                    delay = policy["backoff_seconds"] * 2**used
                    if status == 429:
                        delay = policy["retry_429_seconds"]
                        value = headers.get("Retry-After")
                        if value:
                            try:
                                delay = max(0, float(value))
                            except ValueError:
                                try:
                                    delay = max(
                                        0, parsedate_to_datetime(value).timestamp() - time.time()
                                    )
                                except (TypeError, ValueError, OverflowError):
                                    pass
                        if not math.isfinite(delay):
                            delay = policy["retry_429_seconds"]
                    reason = f"HTTP {status or 'timeout'}: retry limit reached"
                    self._finish(
                        attempt_id,
                        status,
                        "retryable failure",
                        pause_until=time.time() + delay,
                        blocked=reason if used >= policy["max_retries"] else None,
                    )
                    if used >= policy["max_retries"]:
                        raise APIStopped(reason)
                    continue
                if status != 200:
                    self._finish(
                        attempt_id,
                        status,
                        "unexpected HTTP status",
                        blocked=f"HTTP {status}; inspect request",
                    )
                    raise APIStopped(f"HTTP {status}; stopped without retry")
                try:
                    result = json.loads(text)
                except ValueError:
                    self._finish(
                        attempt_id, status, "invalid JSON", blocked="invalid JSON response"
                    )
                    raise APIStopped("invalid JSON response; stopped without retry") from None
                if self.key in text or self.key in json.dumps(result, ensure_ascii=False):
                    self._finish(
                        attempt_id,
                        status,
                        "credential reflected",
                        blocked="credential reflected by provider",
                    )
                    raise APIStopped("provider reflected a credential; response was not saved")
                # HTTP200 JSON was already persisted before notice classification.
                # Replacing it again adds no evidence and can hit Windows reader/AV locks.
                self._finish(attempt_id, status, "success")
                return result, attempt_id

    @property
    def prefix(self):
        return "" if self.base.endswith("/v1") else "/v1"

    def models(self, batch_id):
        result, _ = self.request("GET", self.prefix + "/models", "models:" + batch_id)
        names = [item.get("id") for item in result.get("data", [])]
        if self.model not in names:
            raise APIStopped("specified model absent from model list; no generation sent")
        return {"specified_model": self.model, "confirmed": True, "available_count": len(names)}

    def generate(self, job_id, messages, max_tokens=800):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": max_tokens,
            "stream": False,
        }
        # Recover a response persisted by transport even if the caller crashed
        # before importing its result. Matching the actual body hash is required.
        body_sha = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
        response = attempt = None
        with self.connect() as conn:
            saved = conn.execute(
                "SELECT id,input_sha FROM attempts WHERE task=? AND status=200 AND outcome='success' ORDER BY started DESC",
                (job_id,),
            ).fetchall()
        for ident, source_sha in saved:
            cached = self.state / "responses" / (ident + ".json")
            if source_sha == body_sha and cached.exists():
                response = json.loads(cached.read_text(encoding="utf-8"))
                attempt = ident
                break
        if response is None:
            response, attempt = self.request(
                "POST", self.prefix + "/chat/completions", job_id, payload
            )

        def stop(reason):
            self._finish(attempt, 200, reason, blocked=reason)
            raise APIStopped(reason)

        policy, _ = read_policy()
        aliases = policy.get("approved_model_aliases", {})
        allowed_alias = aliases.get(self.model)
        if allowed_alias not in (None, "gemini-3.8-flash-tiered"):
            stop("unapproved model mapping in API skill")
        returned_model = response.get("model")
        if returned_model != self.model and (
            allowed_alias is None or returned_model != allowed_alias
        ):
            stop("response model differs from specified model; verify gateway mapping")
        choices = response.get("choices", [])
        if not choices or choices[0].get("finish_reason") != "stop":
            stop("missing or truncated completion; no automatic content retry")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            stop("completion contains no usable text")
        return content, {
            "model": self.model,
            "requested_model": self.model,
            "returned_model": returned_model,
            "mapping_basis": "user-approved exact proxy mapping; local proxy request log and successful minimal dialogue"
            if returned_model != self.model
            else "exact model name",
            "reasoning": "provider-default",
            "provider": "openai-compatible",
            "execution_id": attempt,
            "provider_request_id": response.get("id"),
            "usage": response.get("usage"),
        }
