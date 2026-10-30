import copy

from tracemind.experiments.input_audit import audit_input


def sources():
    old = {"id": "gemini_v1_G001-T1-S01-U01", "role": "client", "text": "a known event"}
    cp = {
        "previous_sessions": [{"utterances": [old]}],
        "current_dialogue": [],
        "latest_client": {"id": "now", "role": "client", "text": "current"},
    }
    context = {
        "history": {"memory": "", "evidence": [old.copy()]},
        "current": {k: cp[k] for k in ("current_dialogue", "latest_client")},
        "instruction": "reply",
    }
    return old, cp, context


def test_saved_history_cannot_hide_future_or_other_case_even_when_job_hash_matches():
    old, cp, ctx = sources()
    assert audit_input(ctx, cp, "temporalrag") == []
    for hidden in ("gemini_v1_G001-T1-S06-U01", "gemini_v1_G002-T1-S01-U01", "now"):
        wrong = copy.deepcopy(ctx)
        wrong["history"]["evidence"][0]["id"] = hidden
        assert "unavailable or future historical evidence" in audit_input(wrong, cp, "temporalrag")


def test_memory_supports_and_summary_citations_obey_the_same_cutoff():
    old, cp, ctx = sources()
    ctx["history"] = {
        "evidence": [],
        "memory": {
            "evidence": [{"id": old["id"], "source": "client", "text": "a known"}],
            "statements": [{"support_ids": [old["id"]], "refute_ids": []}],
        },
    }
    assert audit_input(ctx, cp, "evolution") == []
    ctx["history"]["memory"]["statements"][0]["refute_ids"] = ["future"]
    assert audit_input(ctx, cp, "evolution")
    ctx["history"]["memory"] = "gemini_v1_G001-T1-S06-U01 says future"
    assert "summary cites unavailable or other-case evidence" in audit_input(ctx, cp, "summary")


def test_condition_access_and_private_input_fields_are_checked_separately():
    old, cp, ctx = sources()
    assert audit_input(ctx, cp, "current")
    assert audit_input(ctx, cp, "oracle", oracle_ids=[])  # Unapproved diagnostic snippet.
    assert audit_input(ctx, cp, "oracle", oracle_ids=[old["id"]]) == []
    ctx["private_current"] = {"future": "hidden"}
    assert "unexpected answer input fields" in audit_input(ctx, cp, "temporalrag")
    del ctx["private_current"]
    ctx["history"]["evidence"][0]["text"] = "rewritten event"
    assert "historical text differs from its source" in audit_input(ctx, cp, "temporalrag")
