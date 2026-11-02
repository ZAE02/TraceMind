import json
from urllib.error import HTTPError

import pytest

import tracemind.api_client as module


class Clock:
    value = 1793300000.0

    def time(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class Response:
    status = 200
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, maximum):
        return b'{"ok":true}'


class Transport:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def api(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "base_url": "http://127.0.0.1:8045",
                "model": "gemini-3.8-flash",
                "api_key": "test-secret",
            }
        )
    )
    clock = Clock()
    monkeypatch.setattr(module.time, "time", clock.time)
    monkeypatch.setattr(module.time, "sleep", clock.sleep)
    return module.LimitedAPI(tmp_path / "state", config), clock


def test_single_flight_denies_parallel_process_lock(tmp_path):
    with module.single_flight(tmp_path / "lock"):
        with pytest.raises(module.APIStopped, match="single-flight"):
            with module.single_flight(tmp_path / "lock"):
                pytest.fail("must not enter")


def test_429_wait_persists_and_attempts_count(api):
    client, clock = api
    client.opener = Transport([HTTPError("http://local", 429, "throttled", {}, None), Response()])
    client.request("GET", "/models", "one")
    with client.connect() as conn:
        starts = conn.execute("SELECT started,status FROM attempts ORDER BY started").fetchall()
    assert [row[1] for row in starts] == [429, 200]
    assert starts[1][0] - starts[0][0] >= 300
    client.opener = Transport([Response()])
    client.request("GET", "/models", "two")
    with client.connect() as conn:
        final = conn.execute("SELECT max(started) FROM attempts").fetchone()[0]
    assert final - starts[-1][0] >= module.read_policy()[0]["interval_seconds"]


def test_auth_failure_stops_all_future_tasks_without_retry(api):
    client, _ = api
    client.opener = Transport([HTTPError("http://local", 401, "test-secret", {}, None)])
    with pytest.raises(module.APIStopped, match="401"):
        client.request("GET", "/models", "one")
    with pytest.raises(module.APIStopped, match="blocked"):
        client.request("GET", "/models", "another")
    assert client.opener.calls == 1
    assert b"test-secret" not in client.database.read_bytes()


def test_account_prose_false_positive_and_real_notice_preserved(api):
    client, _ = api

    class Ordinary(Response):
        def read(self, maximum):
            return b'{"thinking":"without an exhaustive account of the verification process", "ok":true}'

    client.opener = Transport([Ordinary()])
    client.request("GET", "/fixture", "ordinary-prose")
    assert module.account_service_notice("Your account requires verification")
    assert module.account_service_notice("Account verification is required")
    assert module.account_service_notice("Service has been disabled")

    class Notice(Response):
        def read(self, maximum):
            return b'{"error":{"message":"Account verification required"}}'

    client.opener = Transport([Notice()])
    with pytest.raises(module.APIStopped, match="account/service stop"):
        client.request("GET", "/fixture", "genuine-notice")
    with client.connect() as conn:
        ident = conn.execute("SELECT id FROM attempts WHERE task='genuine-notice'").fetchone()[0]
    preserved = json.loads((client.state / "responses" / (ident + ".json")).read_text())
    assert preserved["error"]["message"] == "Account verification required"
    with pytest.raises(module.APIStopped, match="blocked"):
        client.request("GET", "/fixture", "after-notice")


def test_success_response_is_persisted_only_once(api, monkeypatch):
    from tracemind.benchmark import builder

    client, _ = api
    original, writes = builder.write, []

    def record(path, value):
        writes.append(path)
        return original(path, value)

    monkeypatch.setattr(builder, "write", record)
    client.opener = Transport([Response()])
    client.request("GET", "/fixture", "one-save")
    assert client.opener.calls == 1 and len(writes) == 1


def test_response_model_mismatch_is_not_accepted_or_retried(api):
    client, _ = api

    class AliasResponse(Response):
        def read(self, maximum):
            return json.dumps(
                {
                    "model": "unconfirmed-alias",
                    "choices": [{"finish_reason": "stop", "message": {"content": "sample"}}],
                }
            ).encode()

    client.opener = Transport([AliasResponse()])
    with pytest.raises(module.APIStopped, match="model differs"):
        client.generate("job", [{"role": "user", "content": "input"}])
    with pytest.raises(module.APIStopped, match="blocked"):
        client.request("GET", "/models", "another")
    assert client.opener.calls == 1
    assert len(list((client.state / "responses").glob("*.json"))) == 1


def test_user_approved_exact_alias_preserves_both_model_names(api):
    client, _ = api

    class MappedResponse(Response):
        def read(self, maximum):
            return json.dumps(
                {
                    "model": "gemini-3.8-flash-tiered",
                    "choices": [{"finish_reason": "stop", "message": {"content": "valid sample"}}],
                }
            ).encode()

    client.opener = Transport([MappedResponse()])
    text, provenance = client.generate("mapped-job", [{"role": "user", "content": "input"}])
    assert text == "valid sample"
    assert provenance["requested_model"] == "gemini-3.8-flash"
    assert provenance["returned_model"] == "gemini-3.8-flash-tiered"
    assert provenance["mapping_basis"]
    same, source = client.generate("mapped-job", [{"role": "user", "content": "input"}])
    assert same == text and source["execution_id"] == provenance["execution_id"]
    assert client.opener.calls == 1


def test_three_failures_stop_and_optional_daily_budget_counts_retries(api, monkeypatch):
    client, _ = api
    client.opener = Transport(
        [HTTPError("http://local", 503, "unavailable", {}, None) for _ in range(3)]
    )
    with pytest.raises(module.APIStopped, match="limit reached"):
        client.request("GET", "/models", "same")
    assert client.opener.calls == 3
    with pytest.raises(module.APIStopped, match="blocked"):
        client.request("GET", "/models", "same")
    with client.connect() as conn:
        conn.execute("UPDATE control SET blocked=NULL")
        last = conn.execute(
            "SELECT started,day,skill_sha,input_sha FROM attempts LIMIT 1"
        ).fetchone()
        conn.executemany(
            "INSERT INTO attempts(id,task,started,day,skill_sha,input_sha) VALUES(?,?,?,?,?,?)",
            [(str(i), str(i), *last) for i in range(97)],
        )
    read = module.read_policy

    def bounded_policy(*args, **kwargs):
        policy, sha = read(*args, **kwargs)
        return {**policy, "daily_limit": 100}, sha

    monkeypatch.setattr(module, "read_policy", bounded_policy)
    with pytest.raises(module.APIStopped, match="daily"):
        client.request("GET", "/models", "new")
    assert client.opener.calls == 3


def test_unlimited_daily_policy_keeps_count_and_allows_more_than_100(api):
    client, _ = api
    client.opener = Transport([Response()])
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = module.time.time()
    day = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).date().isoformat()
    with client.connect() as conn:
        conn.executemany(
            "INSERT INTO attempts(id,task,started,day,skill_sha,input_sha) VALUES(?,?,?,?,?,?)",
            [(str(i), str(i), now - 120, day, "historical", "hash") for i in range(105)],
        )
    client.request("GET", "/models", "new-after-105")
    assert client.opener.calls == 1
    with client.connect() as conn:
        assert conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 106


def test_stop_marker_prevents_sending_a_request(api):
    client, _ = api
    client.opener = Transport([Response()])
    (client.state / "STOP").write_text("pause")
    with pytest.raises(module.APIStopped, match="STOP"):
        client.request("GET", "/models", "must-not-send")
    assert client.opener.calls == 0
    with client.connect() as conn:
        assert conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0


def test_unicode_escaped_credential_reflection_is_not_saved(api):
    client, _ = api

    class Reflected(Response):
        def read(self, maximum):
            return b'{"value":"\\u0074est-secret"}'

    client.opener = Transport([Reflected()])
    with pytest.raises(module.APIStopped, match="reflected"):
        client.request("GET", "/models", "reflection")
    assert not list((client.state / "responses").glob("*.json"))
    assert b"test-secret" not in client.database.read_bytes()


def test_role_context_bounded_and_private_cards_absent_for_counselor():
    from tracemind.api_batch import bounded_context

    rows = [
        {"id": str(i), "role": "client" if i % 2 else "counselor", "text": "utterance"}
        for i in range(16)
    ]
    context = {
        "case_id": "case",
        "session": 6,
        "instruction": "respond",
        "previous_sessions": [{"utterances": rows} for _ in range(5)],
        "current_dialogue": rows,
    }
    selected = bounded_context(
        {"purpose": "dialogue:case:S6:U16", "role": "counselor", "allowed_context": context}
    )
    assert len(selected["current_dialogue"]) == 9
    assert "previous_sessions" not in selected
    assert "profile" not in selected and "experienced_events" not in selected
