"""Report actual locked human outcomes; never substitute model judgments."""

from collections import Counter, defaultdict


def human_report(store):
    report = {"review_kind": "human", "progress": store.statistics(), "outcomes": None}
    if not report["progress"]["complete"]:
        return report
    rows = store.completed_reviews()
    by_packet = defaultdict(list)
    preferences = Counter()
    for row in rows:
        by_packet[row["packet_id"]].append(row["assessment"]["preference"])
        choice = row["assessment"]["preference"]
        preferences[row["mapping"][choice] if choice != "tie" else "tie"] += 1
    if any(len(pair) != 2 for pair in by_packet.values()):
        raise ValueError("completed paired review needs two distinct reviewers per packet")
    pairs = list(by_packet.values())
    n = len(pairs)
    pa = Counter(a for a, b in pairs)
    pb = Counter(b for a, b in pairs)
    observed = sum(a == b for a, b in pairs) / n
    expected = sum(pa[label] * pb[label] for label in ("a", "b", "tie")) / (n * n)
    report["outcomes"] = {
        "human_judgments": len(rows),
        "paired_packets": n,
        "preferences": dict(preferences),
        "agreement": observed,
        "cohen_kappa": (observed - expected) / (1 - expected) if expected < 1 else None,
        "note": "Human quality preferences; candidate access attribution is reported separately.",
    }
    return report
