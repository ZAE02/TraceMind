"""Bounded API contracts for the production graph, distinct from legacy jobs."""


def stage_contract(stage):
    strings = {"type": "array", "maxItems": 8, "items": {"type": "string"}}
    if stage == "extract":
        return (
            {
                "type": "object",
                "required": ["observations"],
                "properties": {
                    "observations": {
                        "type": "array",
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "required": ["text", "evidence_ids"],
                            "properties": {"text": {"type": "string"}, "evidence_ids": strings},
                            "additionalProperties": False,
                        },
                    }
                },
                "additionalProperties": False,
            },
            "从当前公开表达提取观察和待澄清线索，区分陈述与推断。引用只能来自输入发言ID；没有证据可不引用，不编造历史或诊断。",
        )
    if stage == "conflict":
        return (
            {
                "type": "object",
                "required": ["supported", "challenged", "unresolved"],
                "properties": {k: strings for k in ("supported", "challenged", "unresolved")},
                "additionalProperties": False,
            },
            "根据公开证据与已有认识，分别列出支持、挑战和未解问题；没有变化可空。当前表达只用于提出待核对线索，不把猜测当作事实。",
        )
    if stage == "respond":
        return (
            {
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string", "minLength": 1}},
                "additionalProperties": False,
            },
            "仅依据提供的公开输入和证据记忆写一至三句自然中文咨询回应。区分事实与推测，允许纠正，不诊断、不暴露内部标识。",
        )
    if stage != "revise":
        raise ValueError("unknown workflow stage")
    statement = {
        "type": "object",
        "required": ["id", "kind", "text", "status", "support_ids", "refute_ids", "supersedes"],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "kind": {"enum": ["event", "psychological_state", "hypothesis", "unresolved_question"]},
            "text": {"type": "string", "minLength": 1},
            "status": {
                "enum": ["proposed", "supported", "challenged", "superseded", "inapplicable"]
            },
            **{k: strings for k in ("support_ids", "refute_ids", "supersedes")},
        },
        "additionalProperties": False,
    }
    return (
        {
            "type": "object",
            "required": ["evidence", "statements"],
            "properties": {
                "evidence": strings,
                "statements": {"type": "array", "maxItems": 8, "items": statement},
            },
            "additionalProperties": False,
        },
        "生成追加式记忆补丁。evidence仅列history.evidence中本次新增的发言ID，不提供文本时间，不重复history.memory已有证据；statements只新增，不覆盖已有记录。每条陈述须引用真实支持或反驳证据，心理事实不能仅由咨询师推测支持。新ID需独立且不与history.memory重复；修正通过supersedes引用旧陈述。保留不确定性和待澄清问题。",
    )
