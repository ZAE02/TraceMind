"""Dispatch only explicitly enabled v2 jobs to case-isolated file mailboxes.

No agent is launched here; results must be produced by registered task identities.
"""

from __future__ import annotations

import json
import msvcrt
import os
import re
import time
import uuid
from pathlib import Path

import psycopg

from tracemind.benchmark.builder import BenchmarkBuilder
from tracemind.config import LOCAL, dsn
from tracemind.jobs import JobQueue

BRIDGE = LOCAL / "scoped"
CASE = re.compile(r"v2_(G\d{3})-T[1-4]\Z")
GROUP = re.compile(r"G\d{3}\Z")
JOB = re.compile(r"[0-9a-f]{64}\Z")
TASK = re.compile(r"/root/(?:[a-z0-9_]+/)*[a-z0-9_]+\Z")
DIALOGUE = re.compile(r"dialogue:(v2_G\d{3}-T[1-4]):S([1-6]):U([1-9]|1[0-6])\Z")
ANCHOR = re.compile(r"anchor:(G\d{3}):S([246]):v2\Z")


def read_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        # A single lock owner writes, and an existing job file is never rewritten.
        if not path.exists():
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_control() -> dict:
    control = read_json(BRIDGE / "control.json")
    if not isinstance(control, dict):
        raise ValueError("control must be an object")
    groups = control.get("enabled_groups")
    cases = control.get("enabled_cases")
    workers = control.get("worker_registry")
    anchors = control.get("anchor_registry")
    if not isinstance(groups, list) or any(
        not isinstance(x, str) or not GROUP.fullmatch(x) for x in groups
    ):
        raise ValueError("invalid enabled_groups")
    if not isinstance(cases, list) or any(
        not isinstance(x, str) or not CASE.fullmatch(x) or CASE.fullmatch(x).group(1) not in groups
        for x in cases
    ):
        raise ValueError("invalid enabled_cases")
    if not isinstance(workers, dict) or not isinstance(anchors, dict):
        raise ValueError("worker_registry and anchor_registry must be objects")
    identities: list[str] = []
    for case, roles in workers.items():
        if (
            not isinstance(case, str)
            or not CASE.fullmatch(case)
            or not isinstance(roles, dict)
            or set(roles) != {"client", "counselor"}
        ):
            raise ValueError("invalid worker registry entry")
        for task in roles.values():
            if not isinstance(task, str) or not TASK.fullmatch(task):
                raise ValueError("invalid worker task identity")
            identities.append(task)
    for job_id, task in anchors.items():
        if (
            not isinstance(job_id, str)
            or not JOB.fullmatch(job_id)
            or not isinstance(task, str)
            or not TASK.fullmatch(task)
        ):
            raise ValueError("invalid anchor registry entry")
        identities.append(task)
    if len(identities) != len(set(identities)):
        raise ValueError("each case role and anchor must have a distinct task identity")
    return control


def route(job: dict, control: dict) -> tuple[Path, str | None] | None:
    job_id = job.get("job_id")
    purpose, role, context, scope = (
        job.get(x) for x in ("purpose", "role", "allowed_context", "scope_id")
    )
    if (
        not isinstance(job_id, str)
        or not JOB.fullmatch(job_id)
        or not isinstance(purpose, str)
        or not isinstance(context, dict)
    ):
        return None
    if context.get("dataset_namespace") != "v2":
        return None
    match = DIALOGUE.fullmatch(purpose)
    if match:
        case, session, turn = match.groups()
        group = CASE.fullmatch(case).group(1)
        if (
            case not in control["enabled_cases"]
            or group not in control["enabled_groups"]
            or role != ("client" if int(turn) % 2 else "counselor")
            or context.get("case_id") != case
            or context.get("session") != int(session)
            or scope != f"trajectory:{case}:{role}"
        ):
            return None
        task = control["worker_registry"].get(case, {}).get(role)
        if task is None:
            return None
        return BRIDGE / case / role / f"{job_id}.json", task
    match = ANCHOR.fullmatch(purpose)
    if match:
        group, session = match.groups()
        if (
            group not in control["enabled_groups"]
            or role != "client"
            or scope != f"anchor:v2:{group}:{session}"
        ):
            return None
        return BRIDGE / "anchors" / f"{job_id}.json", control["anchor_registry"].get(job_id)
    return None


def pending_v2(queue: JobQueue) -> list[dict]:
    # Apply the namespace restriction in SQL, before any limit; other datasets cannot starve v2.
    with psycopg.connect(queue.dsn) as conn:
        rows = conn.execute("""SELECT id, request FROM agent_jobs WHERE status = 'pending'
            AND (left(request->>'scope_id', 14) = 'trajectory:v2_'
                 OR left(request->>'scope_id', 10) = 'anchor:v2:')
            ORDER BY created_at, id""").fetchall()
    return [dict(job_id=job_id, **request) for job_id, request in rows]


def import_results(queue: JobQueue, control: dict) -> int:
    count = 0
    for path in sorted((BRIDGE / "results").glob("*.json")):
        result = read_json(path)
        if not isinstance(result, dict) or set(result) != {"job_id", "result", "provenance"}:
            raise ValueError("invalid result envelope")
        job_id = result["job_id"]
        if (
            not isinstance(job_id, str)
            or not JOB.fullmatch(job_id)
            or path.name != f"{job_id}.json"
        ):
            raise ValueError("result filename does not match job")
        stored = queue.get(job_id)
        job = {"job_id": job_id, **stored["request"]}
        destination = route(job, control)
        provenance = result.get("provenance")
        if (
            destination is None
            or destination[1] is None
            or not isinstance(provenance, dict)
            or provenance.get("agent_task") != destination[1]
            or not destination[0].is_file()
            or stored["status"] not in {"pending", "completed"}
        ):
            raise ValueError("result is not authorized for its registered job and destination")
        queue.complete(job_id, result["result"], provenance)
        archive = BRIDGE / "imported" / path.name
        archive.parent.mkdir(parents=True, exist_ok=True)
        if archive.exists():
            if read_json(archive) != result:
                raise ValueError("result archive conflict")
            path.unlink()
        else:
            os.replace(path, archive)
        destination[0].unlink(missing_ok=True)
        count += 1
    return count


def main() -> None:
    BRIDGE.mkdir(parents=True, exist_ok=True)
    with (BRIDGE / "dispatcher.lock").open("a+b") as lock:
        lock.seek(0)
        if lock.read(1) == b"":
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise SystemExit("Another scoped dispatcher is running") from exc
        queue = JobQueue(dsn())
        queue.setup()
        builder = BenchmarkBuilder(LOCAL / "datasets" / "v2", queue, namespace="v2")
        last_advance = 0.0
        while True:
            if (BRIDGE / "STOP").exists():
                return
            control = read_control()
            imported = import_results(queue, control)
            now = time.monotonic()
            if imported or now - last_advance >= 10:
                progress = builder.advance(
                    set(control["enabled_groups"]), cases=set(control["enabled_cases"])
                )
                if progress["materialized_utterances"]:
                    builder.advance(
                        set(control["enabled_groups"]), cases=set(control["enabled_cases"])
                    )
                last_advance = time.monotonic()
                for job in pending_v2(queue):
                    destination = route(job, control)
                    if destination is not None and not destination[0].exists():
                        # Recheck the authoritative status immediately before writing.
                        if queue.get(job["job_id"])["status"] == "pending":
                            atomic_write(destination[0], job)
                if imported:
                    print(f"Imported {imported} scoped result(s).", flush=True)
            time.sleep(1)


if __name__ == "__main__":
    main()
