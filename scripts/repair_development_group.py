"""Archive a rejected development suffix, preserving source and database records.

No API calls. Only S4+ of G001 is eligible; frozen formal data is never repaired.
"""

import re
import shutil
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from tracemind.benchmark.builder import read, write
from tracemind.config import LOCAL, dsn


def main():
    root = LOCAL / "datasets/gemini_v1"
    plan = read(root / "plans/G001.json")
    rejected = read(root / "counterfactual_reviews/G001_S4.json")
    if plan["split"] != "development" or rejected["approved"] is not False:
        raise RuntimeError("requires preserved rejected development review")
    if (root / "protocol/frozen.json").exists():
        raise RuntimeError("cannot repair after formal protocol freeze")
    destination = LOCAL / "repair_archive" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shutil.copytree(root, destination / "dataset_before")
    cases = [f"gemini_v1_G001-T{b}" for b in range(1, 5)]
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        evidence = conn.execute(
            "SELECT id,case_id,session,turn,role,text,occurred_at::text,observed_at::text,metadata,embedding::text FROM evidence WHERE case_id = ANY(%s) AND session >= 4",
            (cases,),
        ).fetchall()
        subjects = [f"experiment:on_demand:{case}:{case}-CP{cp}" for case in cases for cp in (2, 3)]
        evolution = [f"experiment:evolution:{case}" for case in cases]
        memory = conn.execute(
            "SELECT * FROM tracemind_memory_revisions WHERE subject_id = ANY(%s) OR (subject_id = ANY(%s) AND revision >= 4)",
            (subjects, evolution),
        ).fetchall()
        write(destination / "database_records.json", {"evidence": evidence, "memory": memory})
        conn.execute("DELETE FROM evidence WHERE case_id = ANY(%s) AND session >= 4", (cases,))
        conn.execute(
            "DELETE FROM tracemind_memory_revisions WHERE subject_id = ANY(%s) OR (subject_id = ANY(%s) AND revision >= 4)",
            (subjects, evolution),
        )
    moved = []
    for path in list(root.rglob("*.json")):
        relative = path.relative_to(root).as_posix()
        affected = bool(re.search(r"gemini_v1_G001-T[1-4]-CP[23](?:\.|/)", relative))
        affected |= bool(re.search(r"gemini_v1_G001-T[1-4](?:/|_)S0[456]\.json$", relative))
        affected |= relative in (
            "counterfactual_reviews/G001_S4.json",
            "counterfactual_reviews/G001_S6.json",
        )
        # On-demand CP filenames are session numbers, not checkpoint indexes.
        affected |= bool(
            re.search(r"snapshots/on_demand/gemini_v1_G001-T[1-4]/CP[46]\.json$", relative)
        )
        if affected:
            target = destination / "invalidated" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            path.replace(target)
            moved.append(relative)
    record = {
        "reason": rejected,
        "group": "G001",
        "first_session": 4,
        "source_backup": str(destination / "dataset_before"),
        "invalidated": moved,
        "database_evidence_archived": len(evidence),
        "database_memory_archived": len(memory),
        "policy": "shared-current-events-v2; regenerate and independently review; no approval override",
    }
    write(destination / "repair.json", record)
    write(root / "project/development_repair.json", record)
    print(f"Archived development suffix: {len(moved)} files, {len(evidence)} evidence rows")


if __name__ == "__main__":
    main()
