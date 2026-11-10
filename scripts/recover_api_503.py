"""Import the single task explicitly authorized in the API skill for 503 recovery."""

import argparse

from tracemind.api_client import (
    CONCURRENT_STOP_MARKER,
    RETRY_503_BLOCK,
    APIStopped,
    LimitedAPI,
    read_policy,
    single_flight,
)
from tracemind.benchmark.builder import read, write
from tracemind.config import LOCAL, dsn
from tracemind.jobs import JobQueue, digest


def recover(batch_id):
    api_dir = LOCAL / "api"
    directory = api_dir / "batches" / batch_id
    stop = api_dir / "STOP"
    with single_flight(api_dir / "batch.lock"):
        policy, _ = read_policy()
        authorization = policy.get("recovery_503_once")
        if authorization is None:
            raise APIStopped("no active one-time 503 recovery authorization")
        task = authorization["task"]
        api = LimitedAPI(recovery_503=True)
        queue = JobQueue(dsn(), model=api.model, reasoning="provider-default")
        job = queue.get(task)
        source = read(directory / "inputs" / f"{task}.json")
        if (
            digest(job["request"]) != task
            or not job["request"]["purpose"].startswith("dialogue:")
            or job["status"] not in {"pending", "completed"}
            or source["job_id"] != task
            or digest(source["actual_messages"]) != source["actual_input_sha256"]
            or stop.read_text(encoding="utf-8").strip() != CONCURRENT_STOP_MARKER
        ):
            raise APIStopped("original task, input or STOP evidence does not match recovery scope")
        stop_mtime = stop.stat().st_mtime_ns
        text, provenance = api.generate(task, source["actual_messages"], max_tokens=600)
        artifact = {**source, "result": {"text": text}, "provenance": provenance}
        queue.complete(task, artifact["result"], provenance)
        artifact_path = directory / f"{task}.json"
        if artifact_path.exists() and read(artifact_path) != artifact:
            raise APIStopped("existing recovery artifact differs from the validated response")
        if not artifact_path.exists():
            write(artifact_path, artifact)
        with api.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id,status,outcome,input_sha FROM attempts WHERE task=? ORDER BY started",
                (task,),
            ).fetchall()
            sha = authorization["input_sha"]
            if (
                len(rows) != 4
                or any(row[1:] != (503, "retryable failure", sha) for row in rows[:3])
                or rows[-1] != (provenance["execution_id"], 200, "success", sha)
                or stop.stat().st_mtime_ns != stop_mtime
            ):
                raise APIStopped("recovery ledger or STOP marker changed; keep batch stopped")
            if conn.execute(
                "UPDATE control SET blocked=NULL WHERE id=1 AND blocked=?", (RETRY_503_BLOCK,)
            ).rowcount != 1:
                raise APIStopped("global stop reason changed; keep batch stopped")
        stop.unlink()
        print("Original task recovered and imported with four preserved attempts.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("batch_id")
    args = parser.parse_args()
    if not args.batch_id or not all(c.isalnum() or c in "-_" for c in args.batch_id):
        parser.error("invalid batch ID")
    try:
        recover(args.batch_id)
    except APIStopped as exc:
        raise SystemExit(str(exc)) from None
