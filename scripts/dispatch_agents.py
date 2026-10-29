"""Bridge file jobs to an active Codex role worker; never invokes an LLM API."""

from __future__ import annotations

import json
import os
import time

from tracemind.benchmark.builder import BenchmarkBuilder, read, write
from tracemind.config import LOCAL, dsn
from tracemind.jobs import JobQueue


def main():
    queue = JobQueue(dsn())
    queue.setup()
    root = LOCAL / "datasets/v1"
    bridge = LOCAL / "dispatch"
    bridge.mkdir(parents=True, exist_ok=True)
    lock = (bridge / "dispatcher.lock").open("a+b")
    if os.name == "nt":
        import msvcrt

        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise SystemExit("Another dispatcher is running") from exc
    else:
        import fcntl

        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    builder = BenchmarkBuilder(root, queue)
    accepted = 0
    last_advance = 0.0
    while not (bridge / "STOP").exists():
        control = bridge / "control.json"
        enabled = (
            set(read(control).get("enabled_groups", ["G001"])) if control.exists() else {"G001"}
        )
        for result_file in sorted((bridge / "results").glob("*.json")):
            result = read(result_file)
            job = queue.get(result["job_id"])
            role = job["request"]["role"]
            if not job["request"]["purpose"].startswith(("dialogue:", "anchor:")):
                raise ValueError("generation worker cannot execute experiment or review jobs")
            expected = {"client": "/root/client_worker", "counselor": "/root/counselor_worker"}
            if role not in expected or result["provenance"]["agent_task"] != expected[role]:
                raise ValueError("unregistered role worker")
            queue.complete(result["job_id"], result["result"], result["provenance"])
            archive = bridge / "imported" / result_file.name
            archive.parent.mkdir(parents=True, exist_ok=True)
            result_file.replace(archive)
            request_file = bridge / role / result_file.name
            request_file.unlink(missing_ok=True)
            accepted += 1
        if not accepted and time.monotonic() - last_advance < 10:
            time.sleep(1)
            continue
        builder.advance(enabled)
        last_advance = time.monotonic()
        for job in queue.pending(2000):
            if not job["purpose"].startswith(("dialogue:", "anchor:")):
                continue
            role = job["role"]
            if role not in {"client", "counselor"}:
                continue
            target = bridge / role / (job["job_id"] + ".json")
            group = job["purpose"].split(":")[1].split("-")[0]
            if group not in enabled:
                target.unlink(missing_ok=True)
                continue
            if not target.exists():
                write(target, job)
        if accepted:
            print(
                json.dumps(
                    {"imported": accepted, "benchmark": builder.audit(verify_jobs=False)},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            accepted = 0
        time.sleep(1)


if __name__ == "__main__":
    main()
