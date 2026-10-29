"""Exploratory cluster-aware summaries and four standalone SVG figures."""
import csv
import html
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

import p1

ROOT = p1.ROOT
OUT = ROOT / "analysis"
OUT.mkdir(parents=True, exist_ok=True)
METRICS_A = ("evidence_grounding", "progress_understanding", "uncertainty_awareness")
METRICS_B = ("history_utilization", "exploration_value", "response_appropriateness")
ERRORS = ("unsupported_presupposition", "redundant_exploration", "missed_progress")
COLORS = {"C0": "#8A9BA8", "C1": "#3A86A8", "C2": "#D29148", "C3": "#6758A7"}


def read_rows():
    key = p1.read(ROOT / "internal/condition_key.json")
    rows = []
    for name in p1.NODES:
        for label in "WXYZ":
            condition = key[name][label]
            for task in "AB":
                first = p1.read(ROOT / "reviews" / name / (label + "_" + task + ".json"))["assessment"]
                second_path = ROOT / "second_reviews" / name / (label + "_" + task + ".json")
                second = p1.read(second_path)["assessment"] if second_path.exists() else None
                row = {"case_id": name[:3], "checkpoint": name, "condition": condition,
                       "candidate_id": label, "task": task,
                       "confidence": first.get("confidence"), "second_reviewed": second is not None}
                for metric in METRICS_A if task == "A" else METRICS_B:
                    row[metric] = first["scores"][metric]
                    row[metric + "_second"] = second["scores"][metric] if second else None
                if task == "B":
                    for err in ERRORS:
                        item = first["errors"][err]
                        row[err] = bool(item["occurred"])
                        row[err + "_access_limited"] = bool(item.get("access_limited", False))
                        row[err + "_second"] = bool(second["errors"][err]["occurred"]) if second else None
                        row[err + "_quote"] = item.get("trigger_quote")
                        row[err + "_evidence_ids"] = item.get("evidence_ids", [])
                rows.append(row)
    return rows


def mean(values):
    return sum(values) / len(values) if values else 0


def bootstrap(rows, fn, seed=271828, repeats=4000):
    cases = sorted({r["case_id"] for r in rows})
    rng = random.Random(seed)
    by_case = {cid: [r for r in rows if r["case_id"] == cid] for cid in cases}
    samples = []
    for _ in range(repeats):
        sample = [row for cid in rng.choices(cases, k=len(cases)) for row in by_case[cid]]
        samples.append(fn(sample))
    samples.sort()
    return samples[int(0.025 * repeats)], samples[int(0.975 * repeats)]


def svg_escape(value):
    return html.escape(str(value), quote=True)


def grouped_bars(filename, title, metric_labels, series, max_y=4, note="Case-clustered 95% bootstrap intervals; n=8 cases", percent=False):
    width, height = 960, 560
    left, right, top, bottom = 80, 230, 75, 100
    plot_w, plot_h = width - left - right, height - top - bottom
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="#fff"/>',
             f'<text x="{left}" y="35" font-size="23" font-family="Arial" font-weight="bold">{svg_escape(title)}</text>',
             f'<text x="{left}" y="55" font-size="12" fill="#555" font-family="Arial">{svg_escape(note)}</text>']
    for tick in range(max_y + 1):
        y = top + plot_h * (1 - tick / max_y)
        parts += [f'<line x1="{left}" y1="{y:.1f}" x2="{left+plot_w}" y2="{y:.1f}" stroke="#e7e7e7"/>',
                  f'<text x="{left-15}" y="{y+4:.1f}" text-anchor="end" font-size="12" fill="#555">{tick*25 if percent else tick}{"%" if percent else ""}</text>']
    group_w = plot_w / len(metric_labels)
    bar_w = min(30, group_w / 5)
    for g, label in enumerate(metric_labels):
        cx = left + (g + 0.5) * group_w
        parts.append(f'<text x="{cx:.1f}" y="{top+plot_h+25}" text-anchor="middle" font-size="12" font-family="Arial">{svg_escape(label)}</text>')
        for i, condition in enumerate(p1.CONDITIONS):
            value, lo, hi = series[(label, condition)]
            x = cx + (i - 1.5) * (bar_w + 5) - bar_w/2
            y = top + plot_h * (1 - value/max_y)
            h = top + plot_h - y
            color = COLORS[condition]
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w}" height="{h:.1f}" fill="{color}"/>')
            ey1, ey2 = (top + plot_h * (1 - hi/max_y), top + plot_h * (1 - lo/max_y))
            mid = x + bar_w/2
            parts += [f'<line x1="{mid:.1f}" y1="{ey1:.1f}" x2="{mid:.1f}" y2="{ey2:.1f}" stroke="#222" stroke-width="1.2"/>',
                      f'<line x1="{mid-4:.1f}" y1="{ey1:.1f}" x2="{mid+4:.1f}" y2="{ey1:.1f}" stroke="#222"/>',
                      f'<line x1="{mid-4:.1f}" y1="{ey2:.1f}" x2="{mid+4:.1f}" y2="{ey2:.1f}" stroke="#222"/>']
    for i, condition in enumerate(p1.CONDITIONS):
        y = top + 65 + i*35
        parts += [f'<rect x="{width-right+20}" y="{y-12}" width="16" height="16" fill="{COLORS[condition]}"/>',
                  f'<text x="{width-right+45}" y="{y}" font-size="14" font-family="Arial">{condition}</text>']
    parts.append('</svg>')
    (OUT / filename).write_text("\n".join(parts), encoding="utf-8")


def figure_gap(rows):
    width, height = 700, 590
    left, top, plot = 90, 70, 420
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="#fff"/>',
             '<text x="70" y="34" font-size="22" font-family="Arial" font-weight="bold">Figure 4. Understanding–response gap</text>',
             '<text x="70" y="53" font-size="12" fill="#555">Paired checkpoint scores; independent Task A/B calls, not causal</text>']
    for tick in range(5):
        x = left + plot*tick/4
        y = top + plot*(1-tick/4)
        parts += [f'<line x1="{x}" y1="{top}" x2="{x}" y2="{top+plot}" stroke="#eee"/>',
                  f'<line x1="{left}" y1="{y}" x2="{left+plot}" y2="{y}" stroke="#eee"/>',
                  f'<text x="{x}" y="{top+plot+22}" text-anchor="middle" font-size="12">{tick}</text>',
                  f'<text x="{left-15}" y="{y+4}" text-anchor="end" font-size="12">{tick}</text>']
    parts.append(f'<line x1="{left}" y1="{top+plot}" x2="{left+plot}" y2="{top}" stroke="#bbb" stroke-dasharray="4 4"/>')
    by = {(r["checkpoint"], r["condition"], r["task"]): r for r in rows}
    for condition in p1.CONDITIONS:
        xs, ys = [], []
        for name in p1.NODES:
            a, b = by[(name, condition, "A")], by[(name, condition, "B")]
            x, y = a["progress_understanding"], b["exploration_value"]
            xs.append(x); ys.append(y)
            parts.append(f'<circle cx="{left+plot*x/4:.1f}" cy="{top+plot*(1-y/4):.1f}" r="3.5" fill="{COLORS[condition]}" fill-opacity=".35"/>')
        mx, my = mean(xs), mean(ys)
        parts.append(f'<circle cx="{left+plot*mx/4:.1f}" cy="{top+plot*(1-my/4):.1f}" r="9" fill="{COLORS[condition]}" stroke="#222" stroke-width="1.2"/>')
    parts += [f'<text x="{left+plot/2}" y="{height-40}" text-anchor="middle" font-size="14">Task A: Progress Understanding (0–4)</text>',
              f'<text transform="translate(26 {top+plot/2}) rotate(-90)" text-anchor="middle" font-size="14">Task B: Exploration Value (0–4)</text>']
    for i, condition in enumerate(p1.CONDITIONS):
        y = top + 30 + i*34
        parts += [f'<circle cx="{left+plot+40}" cy="{y-5}" r="6" fill="{COLORS[condition]}"/>',
                  f'<text x="{left+plot+54}" y="{y}" font-size="13">{condition}</text>']
    parts.append('</svg>')
    (OUT / "figure4_gap.svg").write_text("\n".join(parts), encoding="utf-8")


def run():
    rows = read_rows()
    if len(rows) != 192:
        raise RuntimeError("Incomplete reviews")
    fields = sorted({k for row in rows for k in row})
    with (OUT / "scores_by_answer.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v for k, v in row.items()})
    summary = {"scores": {}, "errors": {}, "paired_differences": {}, "review_stability": {},
               "sample_unit": "case_id", "independent_cases": 8, "checkpoints": 24,
               "reviewed_answers": 192, "interpretation": "exploratory model-judged results"}
    for task, metrics in (("A", METRICS_A), ("B", METRICS_B)):
        subset = [r for r in rows if r["task"] == task]
        for metric in metrics:
            summary["scores"][metric] = {}
            for condition in p1.CONDITIONS:
                group = [r for r in subset if r["condition"] == condition]
                point = mean([r[metric] for r in group])
                lo, hi = bootstrap(subset, lambda sample: mean([r[metric] for r in sample if r["condition"] == condition]))
                summary["scores"][metric][condition] = {"mean": point, "ci95": [lo, hi], "n_checkpoints": len(group)}
            for pair in (("C3", "C0"), ("C2", "C1"), ("C3", "C2")):
                a, b = pair
                key = metric + ":" + a + "-" + b
                fn = lambda sample: mean([r[metric] for r in sample if r["condition"] == a]) - mean(
                                     [r[metric] for r in sample if r["condition"] == b])
                summary["paired_differences"][key] = {"difference": fn(subset), "ci95": list(bootstrap(subset, fn))}
    b_rows = [r for r in rows if r["task"] == "B"]
    for err in ERRORS:
        summary["errors"][err] = {}
        for condition in p1.CONDITIONS:
            group = [r for r in b_rows if r["condition"] == condition]
            fn = lambda sample: mean([int(r[err]) for r in sample if r["condition"] == condition])
            summary["errors"][err][condition] = {"count": sum(r[err] for r in group), "n": len(group),
                                                   "rate": fn(b_rows), "ci95": list(bootstrap(b_rows, fn)),
                                                   "access_limited_count": sum(r[err] and r[err+"_access_limited"] for r in group)}
    repeated = [r for r in rows if r["second_reviewed"]]
    summary["review_stability"] = {"second_reviewed": len(repeated),
                                   "score_pairs": sum(3 for _ in repeated),
                                   "absolute_score_difference_gt1": sum(abs(r[m] - r[m+"_second"]) > 1
                                       for r in repeated for m in (METRICS_A if r["task"] == "A" else METRICS_B)),
                                   "error_disagreements": sum(r[e] != r[e+"_second"] for r in repeated if r["task"] == "B" for e in ERRORS)}
    p1.write(OUT / "summary.json", summary)
    for task, metrics, file, title in (("A", METRICS_A, "figure1_understanding.svg", "Figure 1. Psychological understanding"),
                                       ("B", METRICS_B, "figure2_response.svg", "Figure 2. Counseling response")):
        series = {(metric, condition): (summary["scores"][metric][condition]["mean"],
                                         *summary["scores"][metric][condition]["ci95"])
                  for metric in metrics for condition in p1.CONDITIONS}
        grouped_bars(file, title, [m.replace("_", " ").title() for m in metrics],
                     {(m.replace("_", " ").title(), c): v for (m, c), v in series.items()})
    error_series = {(e.replace("_", " ").title(), c): tuple(x*4 for x in
                    (summary["errors"][e][c]["rate"], *summary["errors"][e][c]["ci95"]))
                    for e in ERRORS for c in p1.CONDITIONS}
    grouped_bars("figure3_errors.svg", "Figure 3. Observable errors", [e.replace("_", " ").title() for e in ERRORS],
                 error_series, note="Bars show rate on a 0–100% axis; intervals clustered by case", percent=True)
    figure_gap(rows)
    # Preserve exact judged quotes and evidence IDs; no cherry-picked rewrite.
    examples = []
    for err in ERRORS:
        candidates = [r for r in b_rows if r[err] and not r[err+"_access_limited"] and r[err+"_quote"]]
        for row in sorted(candidates, key=lambda r: (r["checkpoint"], r["condition"]))[:3]:
            examples.append({"error": err, "checkpoint": row["checkpoint"], "condition": row["condition"],
                             "candidate_id": row["candidate_id"], "trigger_quote": row[err+"_quote"],
                             "evidence_ids": row[err+"_evidence_ids"]})
    p1.write(OUT / "error_examples.json", examples)
    print("Analysis complete: 192 reviews, 4 figures", flush=True)


if __name__ == "__main__":
    run()
