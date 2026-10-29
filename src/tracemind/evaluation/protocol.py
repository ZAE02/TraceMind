"""Frozen quality rubric; method access is deliberately evaluated separately."""

from __future__ import annotations

from jsonschema import validate

VERSION = "quality-v1"
ERRORS = ("unsupported_assertion", "leading_question", "missed_progress")
DIMENSIONS = ("evidence_utilization", "understanding_update", "response_decision")
RUBRIC = """只依据公开对话评价匿名候选，不猜测方法或输入条件，不将参考方向当唯一答案。
三个0–4维度：证据利用（有据、符合时间与发言主体）；理解更新（区分旧认识、修订、适用范围和未知）；回应决策（贴近当下，开放且具体地推进澄清）。0严重冲突，1关键缺失，2部分合理，3充分且小缺口，4充分准确贴合。
分别判断无依据断言（把未确认原因作为事实）、诱导性提问（把特定解释设为必须接受的前提）、遗漏进展（忽略明确相关且已经获得的认识）。合理试探允许被否定，不因含“是不是”自动判错。自然情绪反映也可有价值，不要求每句话都追问。
错误必须绑定真实公开发言ID。前两类还须逐字引用候选句子；遗漏使用具体遗漏说明和历史证据，不强求不存在的引文。单次回应未复述所有历史不构成遗漏。不要推断答题者是否获得历史，这由另一个独立任务判断。"""

error_schema = {
    "type": "object",
    "properties": {
        "occurred": {"type": "boolean"},
        "quote": {"type": ["string", "null"]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string"},
    },
    "required": ["occurred", "quote", "evidence_ids", "explanation"],
    "additionalProperties": False,
}
candidate_schema = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            "properties": {k: {"type": "integer", "minimum": 0, "maximum": 4} for k in DIMENSIONS},
            "required": list(DIMENSIONS),
            "additionalProperties": False,
        },
        "errors": {
            "type": "object",
            "properties": {k: error_schema for k in ERRORS},
            "required": list(ERRORS),
            "additionalProperties": False,
        },
        "tentative_exploration": {"type": "boolean"},
        "rationale": {"type": "string"},
    },
    "required": ["scores", "errors", "tentative_exploration", "rationale"],
    "additionalProperties": False,
}
QUALITY_SCHEMA = {
    "type": "object",
    "properties": {
        "a": candidate_schema,
        "b": candidate_schema,
        "preference": {"enum": ["a", "b", "tie"]},
        "confidence": {"enum": ["low", "medium", "high"]},
    },
    "required": ["a", "b", "preference", "confidence"],
    "additionalProperties": False,
}
ACCESS_SCHEMA = {
    "type": "object",
    "properties": {
        "access_limited_by_error": {
            "type": "object",
            "properties": {k: {"type": "boolean"} for k in ERRORS},
            "required": list(ERRORS),
            "additionalProperties": False,
        },
        "unavailable_evidence_ids": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["access_limited_by_error", "unavailable_evidence_ids", "rationale"],
    "additionalProperties": False,
}


def validate_quality(result: dict, candidates: dict[str, str], visible_ids: set[str]):
    validate(result, QUALITY_SCHEMA)
    for side in ("a", "b"):
        for kind, error in result[side]["errors"].items():
            if not set(error["evidence_ids"]) <= visible_ids:
                raise ValueError("judge cites unavailable public evidence")
            if error["occurred"]:
                if not error["evidence_ids"] or not error["explanation"].strip():
                    raise ValueError("positive error requires specific evidence and explanation")
                if kind != "missed_progress" and (
                    not error["quote"] or error["quote"] not in candidates[side]
                ):
                    raise ValueError(
                        "positive assertion/question error must quote candidate verbatim"
                    )
