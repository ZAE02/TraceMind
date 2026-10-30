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
    assert final - starts[-1][0] >= 60


def test_auth_failure_stops_all_future_tasks_without_retry(api):
    client, _ = api
    client.opener = Transport([HTTPError("http://local", 401, "test-secret", {}, None)])
    with pytest.raises(module.APIStopped, match="401"):
        client.request("GET", "/models", "one")
    with pytest.raises(module.APIStopped, match="blocked"):
        client.request("GET", "/models", "another")
    assert client.opener.calls == 1
    assert b"test-secret" not in client.database.read_bytes()


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


def test_three_failures_stop_and_daily_budget_counts_retries(api):
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
    with pytest.raises(module.APIStopped, match="daily"):
        client.request("GET", "/models", "new")
    assert client.opener.calls == 3


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
