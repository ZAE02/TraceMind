"""Quarantine scored records whose claimed error quotes are not verbatim."""
from pathlib import Path
import shutil
import p1

ROOT = p1.ROOT


def quarantine(path, answer):
    row = p1.read(path)
    try:
        p1.validate_trigger_quotes(row["assessment"], answer)
    except ValueError:
        target = ROOT / "quarantined_nonverbatim" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise RuntimeError("Existing quarantine target: " + str(target))
        shutil.move(str(path), str(target))
        return 1
    return 0


def main():
    counts = {}
    key = p1.read(ROOT / "internal/condition_key.json")
    total = 0
    for path in (ROOT / "reviews").rglob("*_B.json"):
        name, label = path.parent.name, path.stem[0]
        condition = key[name][label]
        answer = p1.read(p1.FROZEN / "answers" / name / (condition + "_B.json"))["raw_text"]
        total += quarantine(path, answer)
    counts["original_conditions"] = total

    mapping_path = ROOT / "mechanisms/internal_blind_key.json"
    if mapping_path.exists():
        mapping = p1.read(mapping_path)
        total = 0
        for path in (ROOT / "mechanisms/reviews").rglob("*_B.json"):
            name, label = path.parent.name, path.stem[0]
            condition = mapping[name][label][0]
            answer = p1.read(ROOT / "mechanisms/answers" / name / (condition + "_B.json"))["raw_text"]
            total += quarantine(path, answer)
        counts["mechanisms"] = total

    for family, packet_dir, key_file, pattern in (
        ("budget_match", ROOT / "budget_match/blind_packets", ROOT / "budget_match/internal_blind_key.json",
         "budget_match/reviews/*/*.json"),
        ("full_access", ROOT / "full_access/blind_packets", ROOT / "full_access/internal_blind_key.json",
         "full_access/reviews/*/*.json"),
    ):
        if not key_file.exists():
            continue
        total = 0
        for path in ROOT.glob(pattern):
            packet = p1.read(packet_dir / (path.parent.name + ".json"))
            answer = packet["candidates"][path.stem]
            total += quarantine(path, answer)
        counts[family] = total
    print(counts, flush=True)


if __name__ == "__main__":
    main()
