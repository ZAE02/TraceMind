from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from tracemind.api_client import APIStopped
from tracemind.memory import InMemoryMemoryStore
from tracemind.workflows import build_workflow, initial_state
from tracemind.workflows.api import run_api_workflow
from tracemind.workflows.schemas import stage_contract


@pytest.mark.parametrize("stage", ["extract", "conflict", "revise", "respond"])
def test_api_graph_contracts_are_valid(stage):
    schema, instruction = stage_contract(stage)
    Draft202012Validator.check_schema(schema)
    assert instruction and schema["required"]


def test_api_model_configuration_survives_graph_interrupt_and_history_is_bounded():
    class Tokenizer:
        def encode_tokens(self, text):
            return list(text)

        def decode_tokens(self, tokens):
            return "".join(tokens)

    graph = build_workflow(
        InMemoryMemoryStore(),
        InMemorySaver(),
        lambda state: [{"id": "e1", "text": "x" * 5000}],
        Tokenizer(),
    )
    cfg = {"configurable": {"thread_id": "api-test"}}
    result = graph.invoke(
        initial_state("case", {}, ["e1"], model="gemini-3.8-flash", reasoning="provider-default"),
        cfg,
    )
    request = result["__interrupt__"][0].value
    assert request["model"] == "gemini-3.8-flash"
    assert request["output_schema"] == stage_contract("extract")[0]
    result = graph.invoke(Command(resume={"observations": []}), cfg)
    request = result["__interrupt__"][0].value
    assert request["reasoning"] == "provider-default"
    context = request["allowed_context"]
    assert context["history_tokens"] <= 4096
    assert not context["history"]["evidence"]
    assert "previous" not in context and "retrieved" not in context


def test_api_graph_rejects_mismatched_provider_without_sending(tmp_path):
    with pytest.raises(APIStopped, match="designated API model"):
        run_api_workflow(
            SimpleNamespace(model="legacy", reasoning="medium"),
            SimpleNamespace(model="gemini-3.8-flash"),
            thread_id="t",
            subject_id="s",
            input={},
            allowed_ids=[],
            models=None,
            directory=tmp_path,
        )
