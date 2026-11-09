"""Plan or stage one bounded replacement for each unresolved trajectory."""

import argparse
from pathlib import Path

import psycopg

from tracemind.api_client import single_flight
from tracemind.benchmark.builder import read, write
from tracemind.benchmark.review_resolution import effective_approval, label
from tracemind.config import LOCAL, dsn
from tracemind.jobs import digest

VERSION = "cohort-completion-v1"


def plan(root):
    cases = {}
    for path in sorted((root / "dialogues").glob("*/S*.json")):
        session = read(path)
        review = root / "dialogue_reviews" / (label(session) + ".json")
        if review.exists() and not read(review)["approved"] and not effective_approval(root, session):
            cases.setdefault(session["case_id"], session["session"])
    for path in (root / "source_quarantines").glob("*.json"):
        source = read(path)
        if source["status"] == "unresolved":
            case, number = source["case_id"], source["session"]
            cases[case] = min(number, cases.get(case, number))
    return cases


def stage(root, archive, conn, cases):
    if (root / "protocol/frozen.json").exists():
        raise ValueError("the evaluation protocol is already frozen")
    manifest = archive / "manifest.json"
    if manifest.exists():
        records = read(manifest)
    else:
        records = {}
        for case, start in sorted(cases.items()):
            if conn.execute(
                "SELECT EXISTS (SELECT 1 FROM evidence WHERE case_id=%s AND session >= %s)",
                (case, start),
            ).fetchone()[0]:
                raise ValueError(f"replacement would invalidate indexed evidence: {case}")
            relative = Path("dialogues") / case
            first = read(root / relative / f"S{start:02}.json")
            if first.get("generation_revision") == VERSION:
                raise ValueError(f"replacement already attempted: {case}")
            originals, replacements = {}, {}
            for number in range(start, 7):
                dialogue = relative / f"S{number:02}.json"
                current = root / dialogue
                if current.exists():
                    originals[str(dialogue)] = read(current)
                replacements[str(dialogue)] = {
                    **{key: first[key] for key in ("case_id", "group_id", "split", "plan_sha256")},
                    "session": number,
                    "utterances": first["utterances"][:1] if number == start else [],
                    "complete": False,
                    "generation_revision": VERSION,
                }
                for folder in ("dialogue_reviews", "review_resolutions"):
                    artifact = Path(folder) / f"{case}_S{number:02}.json"
                    if (root / artifact).exists():
                        originals[str(artifact)] = read(root / artifact)
                if number in (2, 4, 6) and number > start:
                    ident = f"{case}-CP{number // 2}"
                    if any((root / "experiments" / part / ident).exists()
                           for part in ("candidates", "understanding", "quality")):
                        raise ValueError(f"replacement would invalidate evaluation: {ident}")
                    checkpoint = Path("checkpoints") / (ident + ".json")
                    if (root / checkpoint).exists():
                        originals[str(checkpoint)] = read(root / checkpoint)
            for source in (root / "source_quarantines").glob("*.json"):
                value = read(source)
                if value["case_id"] == case and value["status"] == "unresolved":
                    key = str(source.relative_to(root))
                    originals[key] = value
                    replacements[key] = {**value, "status": "superseded_by_versioned_replacement"}
            for path, value in originals.items():
                write(archive / path, value)
            records[case] = {
                "first_replaced_session": start,
                "originals": {key: digest(value) for key, value in originals.items()},
                "replacements": replacements,
            }
        write(manifest, records)
    for record in records.values():
        replacements = record["replacements"]
        for path, expected in record["originals"].items():
            if digest(read(archive / path)) != expected:
                raise ValueError("archived source hash mismatch")
            target = root / path
            if target.exists() and digest(read(target)) != expected:
                if path in replacements and read(target) == replacements[path]:
                    continue
                raise ValueError("active source changed after replacement was staged")
            if path not in replacements:
                target.unlink(missing_ok=True)
        for path, value in replacements.items():
            write(root / path, value)
    write(root / "project/cohort_completion.json", {
        "version": VERSION,
        "archived_originals": str(archive),
        "first_replaced_session": {
            case: record["first_replaced_session"] for case, record in records.items()
        },
    })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = LOCAL / "datasets/gemini_v1"
    cases = plan(root)
    if args.apply:
        if (root / "project/cohort_completion.json").exists():
            raise SystemExit("replacement cohort already staged")
        with single_flight(LOCAL / "api/batch.lock"), psycopg.connect(dsn()) as conn:
            stage(root, LOCAL / "repair_archive" / VERSION, conn, cases)
    print(f"{len(cases)} trajectories, {sum(7 - start for start in cases.values())} sessions")


if __name__ == "__main__":
    main()
