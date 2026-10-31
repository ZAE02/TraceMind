"""Offline transport tests: no model requests or wall-clock rate-limit waits."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import tracemind.api_client as module


def test_four_slots_with_atomic_global_spacing_and_shared_fatal_stop(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "base_url": "http://127.0.0.1:8045",
                "model": "gemini-3.8-flash",
                "api_key": "fixture",
            }
        )
    )
    # Accelerate only the module's clock; simulated network occupies slots for 60 logical seconds.
    epoch = time.monotonic()
    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(
            time=lambda: 1800000000 + (time.monotonic() - epoch) * 100,
            sleep=lambda seconds: time.sleep(seconds / 100),
        ),
    )
    client = module.LimitedAPI(tmp_path / "state", config)
    mutex = threading.Lock()
    active = maximum = 0

    class Response:
        status, headers = 200, {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return b'{"ok":true}'

    class Transport:
        def open(self, request, timeout):
            nonlocal active, maximum
            with mutex:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.6)
            with mutex:
                active -= 1
            return Response()

    client.opener = Transport()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda n: client.request("GET", "/models", str(n)), range(8)))
    assert len(results) == 8 and 2 <= maximum <= 4
    with client.connect() as conn:
        starts = [row[0] for row in conn.execute("SELECT started FROM attempts ORDER BY started")]
    assert all(b - a >= 10 for a, b in zip(starts, starts[1:]))
    client._finish(results[0][1], 403, "fixture blocked", blocked="HTTP403 fixture")
    with pytest.raises(module.APIStopped, match="blocked"):
        client.request("GET", "/models", "after-fatal")
    with client.connect() as conn:
        assert conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 8


def test_same_task_cannot_retry_concurrently(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "base_url": "http://127.0.0.1:8045",
                "model": "gemini-3.8-flash",
                "api_key": "fixture",
            }
        )
    )
    client = module.LimitedAPI(tmp_path / "state", config)
    with client._request_slot("same-task"):
        with pytest.raises(module.APIStopped, match="single-flight"):
            with client._request_slot("same-task"):
                pytest.fail("parallel retry must not enter")
