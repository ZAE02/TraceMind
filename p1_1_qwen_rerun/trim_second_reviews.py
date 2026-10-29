"""Keep second-pass records aligned with the current prespecified selection."""
import shutil
import p1

ROOT = p1.ROOT
targets = set(p1.second_review_targets())
moved = 0
for path in (ROOT / "second_reviews").rglob("*.json"):
    key = (path.parent.name, path.stem[0], path.stem[2])
    if key not in targets:
        destination = ROOT / "extra_second_reviews" / path.parent.name / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise RuntimeError("Existing destination: " + str(destination))
        shutil.move(str(path), str(destination))
        moved += 1
print({"targets": len(targets), "excluded_records_preserved": moved}, flush=True)
