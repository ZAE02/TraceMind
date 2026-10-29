import pytest

from tracemind.memory import (
    InMemoryMemoryStore,
    MemoryValidationError,
    new_revision,
    validate_revision,
)

NOW = "2026-01-01T00:00:00+00:00"


def evidence(id="e1"):
    return {
        "id": id,
        "text": "An observed event",
        "source": "user",
        "observed_at": NOW,
        "recorded_at": NOW,
    }


def statement(id="s1", **kwargs):
    return {"id": id, "kind": "hypothesis", "text": "A hypothesis", **kwargs}


def test_append_correction_retains_evidence_and_original_statement():
    first = new_revision(
        "person",
        None,
        evidence=[evidence()],
        statements=[statement(status="supported", support_ids=["e1"])],
    )
    second = new_revision(
        "person",
        first,
        evidence=[evidence(), evidence("e2")],
        statements=[
            statement(status="supported", support_ids=["e1"]),
            statement("s2", status="challenged", refute_ids=["e2"], supersedes=["s1"]),
        ],
    )
    store = InMemoryMemoryStore()
    store.append(first)
    store.append(second)
    assert store.history("person") == [first, second]
    assert second.statements[1].supersedes == ("s1",)
    with pytest.raises(MemoryValidationError):
        validate_revision(
            new_revision("person", first, evidence=[evidence()], statements=[]), first
        )


def test_rejects_unknown_evidence_and_unapproved_ids():
    bad = new_revision(
        "person",
        None,
        evidence=[evidence()],
        statements=[statement(status="supported", support_ids=["missing"])],
    )
    with pytest.raises(MemoryValidationError):
        validate_revision(bad)
    good = new_revision("person", None, evidence=[evidence()], statements=[statement()])
    with pytest.raises(MemoryValidationError):
        validate_revision(good, allowed_evidence_ids=set())


def test_rejects_invalid_time_and_contradictory_evidence_roles():
    bad_time = evidence()
    bad_time["observed_at"] = "2026-01-01"
    with pytest.raises(MemoryValidationError):
        validate_revision(new_revision("person", None, evidence=[bad_time], statements=[]))
    bad_roles = new_revision(
        "person",
        None,
        evidence=[evidence()],
        statements=[statement(support_ids=["e1"], refute_ids=["e1"])],
    )
    with pytest.raises(MemoryValidationError):
        validate_revision(bad_roles)


def test_workflow_pauses_for_external_jobs_and_persists_before_response():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    from tracemind.workflows import build_workflow, initial_state

    store = InMemoryMemoryStore()
    graph = build_workflow(store, InMemorySaver())
    config = {"configurable": {"thread_id": "test-run"}}
    state = graph.invoke(initial_state("person", {"message": "hello"}, ["e1"]), config)
    assert state["__interrupt__"][0].value["purpose"] == "extract"
    state = graph.invoke(Command(resume={"events": []}), config)
    assert state["__interrupt__"][0].value["purpose"] == "conflict"
    state = graph.invoke(Command(resume={"conflicts": []}), config)
    assert state["__interrupt__"][0].value["purpose"] == "revise"
    state = graph.invoke(
        Command(
            resume={
                "evidence": [evidence()],
                "statements": [statement(status="supported", support_ids=["e1"])],
            }
        ),
        config,
    )
    assert state["__interrupt__"][0].value["purpose"] == "respond"
    assert len(store.history("person")) == 1
    state = graph.invoke(Command(resume={"text": "Done"}), config)
    assert state["response"] == {"text": "Done"}


def test_postgres_store_opt_in():
    import os
    from uuid import uuid4

    from tracemind.memory import PostgresMemoryStore

    dsn = os.environ.get("TRACEMIND_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set TRACEMIND_TEST_POSTGRES_DSN to test PostgreSQL")
    store = PostgresMemoryStore(dsn)
    store.setup()
    subject = "test-" + str(uuid4())
    first = new_revision(subject, None, evidence=[evidence()], statements=[statement()])
    store.append(first)
    store.append(first)  # replay is idempotent
    assert store.history(subject) == [first]
    stale = new_revision(subject, None, evidence=[], statements=[])
    with pytest.raises(MemoryValidationError):
        store.append(stale)


def test_graph_passes_retrieved_evidence_to_conflict():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    from tracemind.workflows import build_workflow, initial_state

    graph = build_workflow(
        InMemoryMemoryStore(), InMemorySaver(), lambda state: [{"id": "e1", "text": "observed"}]
    )
    config = {"configurable": {"thread_id": "retrieval"}}
    graph.invoke(initial_state("person", {}, ["e1"]), config)
    state = graph.invoke(Command(resume={"ok": True}), config)
    assert state["__interrupt__"][0].value["allowed_context"]["retrieved"][0]["id"] == "e1"


def test_postgres_checkpoint_survives_reopen_opt_in():
    import os
    from uuid import uuid4

    from langgraph.types import Command

    from tracemind.memory import PostgresMemoryStore
    from tracemind.workflows import build_workflow, initial_state, postgres_checkpointer

    dsn = os.environ.get("TRACEMIND_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set TRACEMIND_TEST_POSTGRES_DSN to test durable graph recovery")
    store = PostgresMemoryStore(dsn)
    store.setup()
    subject = "checkpoint-test-" + str(uuid4())
    cfg = {"configurable": {"thread_id": subject}}

    class FixtureTokenizer:
        def encode_tokens(self, text):
            return list(text)

        def decode_tokens(self, tokens):
            return "".join(tokens)

    tokenizer = FixtureTokenizer()

    def fixture_resolver(patch, state):
        assert patch["evidence"] == ["e1"]
        return {**patch, "evidence": [evidence()]}

    with postgres_checkpointer(dsn) as saver:
        graph = build_workflow(store, saver, lambda state: [], tokenizer, fixture_resolver)
        output = graph.invoke(initial_state(subject, {"fixture": True}, ["e1"]), cfg)
        assert output["__interrupt__"][0].value["purpose"] == "extract"
    with postgres_checkpointer(dsn) as saver:
        graph = build_workflow(store, saver, lambda state: [], tokenizer, fixture_resolver)
        output = graph.invoke(Command(resume={"events": []}), cfg)
        assert output["__interrupt__"][0].value["purpose"] == "conflict"
        graph.invoke(Command(resume={"conflicts": []}), cfg)
        output = graph.invoke(
            Command(resume={"evidence": ["e1"], "statements": [statement()]}), cfg
        )
        assert output["__interrupt__"][0].value["purpose"] == "respond"
    assert len(store.history(subject)) == 1


def test_counselor_only_evidence_does_not_confirm_psychological_claim():
    counselor = evidence()
    counselor["source"] = "counselor"
    supported = new_revision(
        "person",
        None,
        evidence=[counselor],
        statements=[statement(status="supported", support_ids=["e1"])],
    )
    with pytest.raises(MemoryValidationError):
        validate_revision(supported)
    proposed = new_revision(
        "person",
        None,
        evidence=[counselor],
        statements=[statement(status="proposed", support_ids=["e1"])],
    )
    validate_revision(proposed)
