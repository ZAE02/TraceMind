"""Preserve and revise the user-authorized G003 pre-freeze calibration session, offline."""

from pathlib import Path

import psycopg

from tracemind.benchmark.builder import read, write
from tracemind.config import LOCAL, dsn
from tracemind.jobs import digest


def prepare(root: Path, archive: Path):
    case = "gemini_v1_G003-T4"
    session_path = root / "dialogues" / case / "S06.json"
    review_path = root / "dialogue_reviews" / f"{case}_S06.json"
    session, review = read(session_path), read(review_path)
    if session["split"] not in {"development", "calibration"} or review["approved"] is not False:
        raise ValueError("requires rejected pre-freeze calibration session")
    if session.get("generation_revision") or archive.exists():
        raise ValueError("repair already prepared; resume existing revision")
    if digest(session) != review["dialogue_sha256"]:
        raise ValueError("review source mismatch")
    if (root / "protocol/frozen.json").exists():
        raise ValueError("formal protocol already frozen")
    # Current checkpoint freezes at U01: retaining U01-U06 cannot change any checkpoint.
    checkpoint_hashes = {p.name: digest(read(p)) for p in (root / "checkpoints").glob("*.json")}
    write(archive / "session.json", session)
    write(archive / "review.json", review)
    write(archive / "manifest.json", {
        "authorization": "user requested continue after proposed versioned repair",
        "source_sha256": digest(session), "retained_turns": 6,
        "revision": "consultation-frame-v1", "checkpoint_hashes": checkpoint_hashes,
        "policy": "new role-isolated tasks; independent review required; no approval override",
    })
    session["utterances"] = session["utterances"][:6]
    session["complete"] = False
    session["generation_revision"] = "consultation-frame-v1"
    session["revision_source_sha256"] = review["dialogue_sha256"]
    write(session_path, session)
    review_path.unlink()  # Exact rejected record remains in archive and immutable source job.
    assert checkpoint_hashes == {
        p.name: digest(read(p)) for p in (root / "checkpoints").glob("*.json")
    }


if __name__ == "__main__":
    root = LOCAL / "datasets/gemini_v1"
    # Rejected session must never have entered retrieval or experiments.
    with psycopg.connect(dsn()) as conn:
        count = conn.execute(
            "SELECT count(*) FROM evidence WHERE case_id=%s AND session=6",
            ("gemini_v1_G003-T4",),
        ).fetchone()[0]
        if count:
            raise ValueError("session already indexed; explicit dependent-artifact repair required")
    prepare(root, LOCAL / "repair_archive/G003-T4-S06-consultation-frame-v1")
    print("Archived rejected pre-freeze calibration session; retained 6 turns; independent review pending")
