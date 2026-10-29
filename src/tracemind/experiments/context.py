"""Token-budgeted serialization of supplied memory and evidence."""

from __future__ import annotations

import json
from typing import Any


def serialized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pack_context(
    tokenizer,
    evidence: list[dict],
    memory: str = "",
    *,
    current: Any = None,
    instructions: str = "",
    budget: int = 4096,
) -> dict:
    if budget <= 0:
        raise ValueError("budget must be positive")

    def count(value):
        return len(tokenizer.encode_tokens(serialized(value)))

    chosen = {"memory": {} if isinstance(memory, dict) else "", "evidence": []}
    if count(chosen) > budget:
        raise ValueError("budget too small for context envelope")
    # Fit memory by token prefix, then evidence as indivisible, provenance-bearing records.
    if isinstance(memory, dict):
        chosen["memory"] = memory
        if count(chosen) > budget:
            raise ValueError("structured memory projection exceeds budget")
    elif memory:
        tokens = tokenizer.encode_tokens(memory)
        lo, hi = 0, len(tokens)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            candidate = {"memory": tokenizer.decode_tokens(tokens[:mid]), "evidence": []}
            if count(candidate) <= budget:
                lo = mid
            else:
                hi = mid - 1
        chosen["memory"] = tokenizer.decode_tokens(tokens[:lo])
        while count(chosen) > budget and lo:
            lo -= 1
            chosen["memory"] = tokenizer.decode_tokens(tokens[:lo])
    for item in evidence:
        candidate = {**chosen, "evidence": chosen["evidence"] + [item]}
        if count(candidate) <= budget:
            chosen = candidate
    return {
        "history": chosen,
        "history_tokens": count(chosen),
        "current_tokens": count(current),
        "instruction_tokens": count(instructions),
        "budget": budget,
        "dropped_evidence": len(evidence) - len(chosen["evidence"]),
    }
