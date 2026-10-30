"""Independent input-permission checks against frozen public source utterances."""

import re


def audit_input(context, checkpoint, method, *, oracle_ids=()):
    issues = []
    if set(context) != {"history", "current", "instruction"}:
        issues.append("unexpected answer input fields")
    current = {k: checkpoint[k] for k in ("current_dialogue", "latest_client")}
    if context.get("current") != current:
        issues.append("current input differs from frozen checkpoint")
    history = context.get("history", {})
    if not isinstance(history, dict) or set(history) != {"evidence", "memory"}:
        return issues + ["unexpected historical input schema"]
    allowed = {u["id"]: u for s in checkpoint["previous_sessions"] for u in s["utterances"]}
    outer = history["evidence"]
    memory = history["memory"]
    if method == "current" and (outer or memory):
        issues.append("current-only condition received historical information")
    if method in ("temporalrag", "oracle") and memory:
        issues.append("retrieval-only condition received a memory snapshot")
    if method in ("summary", "on_demand", "evolution") and outer:
        issues.append("memory-only condition received extra historical snippets")

    def evidence(rows):
        if not isinstance(rows, list):
            issues.append("invalid evidence list")
            return
        for row in rows:
            source = allowed.get(row.get("id"))
            if source is None:
                issues.append("unavailable or future historical evidence")
                continue
            text = row.get("text")
            if not isinstance(text, str) or not text or not source["text"].startswith(text):
                issues.append("historical text differs from its source")
            if row.get("role", row.get("source")) != source["role"]:
                issues.append("historical speaker differs from its source")

    evidence(outer)
    if method == "oracle" and any(row.get("id") not in oracle_ids for row in outer):
        issues.append("Oracle received evidence outside its approved historical set")
    if isinstance(memory, dict):
        if set(memory) != {"statements", "evidence"}:
            issues.append("unexpected structured-memory fields")
        evidence(memory.get("evidence", []))
        for statement in memory.get("statements", []):
            refs = set(statement.get("support_ids", [])) | set(statement.get("refute_ids", []))
            if not refs <= set(allowed):
                issues.append("memory support/refutation references unavailable history")
    elif isinstance(memory, str):
        # A free-text summary may also cite IDs. Semantic hallucinations without
        # IDs still require the separate understanding/response evaluations.
        ids = re.findall(r"(?:[a-z][a-z0-9_]*_)?G\d{3}-T\d+-S\d{2}-U\d{2}", memory)
        if not set(ids) <= set(allowed):
            issues.append("summary cites unavailable or other-case evidence")
    else:
        issues.append("invalid memory representation")
    return issues
