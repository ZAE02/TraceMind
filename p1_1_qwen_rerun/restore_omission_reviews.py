"""Restore original scores when only an omission quote used the wrong source."""
import p1

ROOT = p1.ROOT
key = p1.read(ROOT / "internal/condition_key.json")
restored = 0
remaining = 0
for path in (ROOT / "quarantined_nonverbatim/reviews").rglob("*_B.json"):
    name, label = path.parent.name, path.stem[0]
    destination = ROOT / "reviews" / name / path.name
    if destination.exists():
        continue
    condition = key[name][label]
    answer = p1.read(p1.FROZEN / "answers" / name / (condition + "_B.json"))["raw_text"]
    row = p1.read(path)
    changed = p1.normalize_omission_quote(row["assessment"], answer)
    typography = p1.normalize_quote_typography(row["assessment"], answer)
    try:
        p1.validate_trigger_quotes(row["assessment"], answer)
    except ValueError:
        remaining += 1
        continue
    row["omission_quote_normalized"] = changed
    row["quote_typography_normalized"] = typography
    row["original_review_quarantine_path"] = str(path.relative_to(ROOT))
    p1.write(destination, row)
    restored += 1
print({"restored": restored, "still_requires_rejudge": remaining}, flush=True)
