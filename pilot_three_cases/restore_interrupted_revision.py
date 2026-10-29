"""Preserve a stopped provisional attempt before the versioned v3 restart."""
import generator as g

root = g.ROOT
destination = root / "revisions/C03/S01/interrupted-r2-v2"
destination.mkdir(parents=True, exist_ok=True)
for name in ("S01.json", "S01.md"):
    source = root / "dialogues/C03" / name
    target = destination / name
    if target.exists():
        raise RuntimeError("Interrupted attempt is already preserved")
    if source.exists():
        target.write_bytes(source.read_bytes())
old = g.read_json(root / "revisions/C03/S01/r1/S01.json")
g.write_json(root / "dialogues/C03/S01.json", old)
g.write_json(root / "audit/agent_reviews/C03/S01.json", g.read_json(root / "revisions/C03/S01/r1/review.json"))
g.render_dialogue(root, old)
lock = root / "generation_logs/runner.lock"
if lock.exists():
    record = g.read_json(lock)
    assert record["pid"] == 42116
    g.write_json(destination / "terminated_runner_lock.json", record)
    lock.unlink()
g.write_json(destination / "interruption.json", {"recorded_at":g.now(), "terminated_pid":42116,
    "reason":"Stopped this own subprocess before loading v3 expression constraints; all returned utterances retained.",
    "unknown_inflight_response":True, "restored_source":"revisions/C03/S01/r1"})
print("Archived interrupted provisional data and restored the rejected r1 input for a v3 r2 repair.")
