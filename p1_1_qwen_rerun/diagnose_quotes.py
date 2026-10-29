import difflib
import statistics
import p1

ROOT = p1.ROOT
key = p1.read(ROOT / "internal/condition_key.json")
translation = str.maketrans({char: '"' for char in "“”‘’「」『』"})
rows = []
for path in (ROOT / "quarantined_nonverbatim/reviews").rglob("*_B.json"):
    name, label = path.parent.name, path.stem[0]
    condition = key[name][label]
    answer = p1.read(p1.FROZEN / "answers" / name / (condition + "_B.json"))["raw_text"]
    for error, row in p1.read(path)["assessment"]["errors"].items():
        quote = row.get("trigger_quote")
        if row.get("occurred") and quote not in answer:
            match = difflib.SequenceMatcher(None, quote.translate(translation),
                                            answer.translate(translation), autojunk=False).find_longest_match()
            rows.append((round(match.size / len(quote), 3), match.size, len(quote), name, label, error))
print("n", len(rows), "median", statistics.median(x[0] for x in rows))
print("ge90", sum(x[0] >= .9 for x in rows), "ge80", sum(x[0] >= .8 for x in rows))
print("low", sorted(rows)[:12])
