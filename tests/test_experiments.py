import json

from tracemind.experiments import ExperimentRunner, pack_context


class Tokenizer:
    def encode_tokens(self, text):
        return list(text)

    def decode_tokens(self, tokens):
        return "".join(tokens)


class Queue:
    def __init__(self):
        self.requests = {}

    def enqueue(self, purpose, role, allowed_context, output_schema, parent_id=None):
        job_id = str(len(self.requests))
        self.requests[job_id] = {"purpose": purpose, "role": role, "context": allowed_context}
        return job_id

    def get(self, job_id):
        return {"status": "pending"}

    def stats(self):
        return []


class Repository:
    def visible(self, *args):
        return []


class Retriever:
    def search(self, *args, **kwargs):
        return []


def test_budget_counts_serialized_metadata_and_separate_current():
    evidence = [
        {"id": "1", "text": "small", "metadata": {"long": "x" * 500}},
        {"id": "2", "text": "ok"},
    ]
    packed = pack_context(
        Tokenizer(),
        evidence,
        "m" * 50,
        current={"text": "c" * 40},
        instructions="instruction",
        budget=160,
    )
    assert packed["history_tokens"] <= 160
    assert packed["history"]["evidence"] == [evidence[1]]
    assert packed["dropped_evidence"] == 1
    assert packed["current_tokens"] > 40
    assert packed["instruction_tokens"] > 0
    assert (
        len(
            json.dumps(packed["history"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        == packed["history_tokens"]
    )


def test_runner_enqueues_real_jobs_without_materializing_pending(tmp_path):
    directory = tmp_path / "checkpoints"
    directory.mkdir()
    cp = {
        "id": "case-CP1",
        "case_id": "case",
        "session": 2,
        "previous_sessions": [],
        "current_dialogue": [],
        "latest_client": {"id": "case-S2-U1", "text": "hello"},
    }
    (directory / "cp.json").write_text(json.dumps(cp), encoding="utf-8")
    queue = Queue()
    result = ExperimentRunner(
        tmp_path,
        queue,
        Repository(),
        Retriever(),
        Tokenizer(),
        __import__("tracemind.memory", fromlist=["InMemoryMemoryStore"]).InMemoryMemoryStore(),
    ).advance()
    assert result["waiting"] > 0
    assert any(job["role"] == "counselor" for job in queue.requests.values())
    assert any(job["role"] == "public evidence annotator" for job in queue.requests.values())
    assert not list((tmp_path / "experiments" / "candidates").glob("*/*.json"))


def test_index_collects_completed_only_and_is_idempotent(tmp_path):
    from tracemind.experiments.indexing import collect_records, index_dialogues
    from tracemind.jobs import digest

    root = tmp_path / "dialogues" / "case"
    root.mkdir(parents=True)
    rows = [
        {
            "id": f"u{i}",
            "role": "client" if i % 2 else "counselor",
            "text": f"utterance {i}",
            "job_id": f"j{i}",
            "result_sha256": digest({"text": f"utterance {i}"}),
        }
        for i in range(1, 17)
    ]
    (root / "S01.json").write_text(
        json.dumps({"case_id": "case", "session": 1, "complete": True, "utterances": rows}),
        encoding="utf-8",
    )
    (root / "S02.json").write_text(
        json.dumps({"case_id": "case", "session": 2, "complete": False, "utterances": []}),
        encoding="utf-8",
    )
    records = collect_records(tmp_path)
    assert len(records) == 16 and records[0].id == "u1"
    assert records[0].metadata["embedding_dimension"] == 1024

    class Repo:
        def existing(self, ids):
            return {r.id: r for r in records}

    class Model:
        def embed(self, texts):
            raise AssertionError("existing evidence was re-embedded")

    assert index_dialogues(tmp_path, Repo(), Model()) == {"existing": 16, "inserted": 0}


def test_projection_and_patch_preserve_unseen_history():
    from tracemind.experiments.context import serialized
    from tracemind.memory import new_revision
    from tracemind.memory.context import apply_patch, project_memory

    now = "2020-01-01T00:00:00+00:00"
    evidence = [
        {"id": f"e{i}", "text": "x" * 250, "source": "user", "observed_at": now, "recorded_at": now}
        for i in range(12)
    ]
    statements = [
        {
            "id": f"s{i}",
            "kind": "unresolved_question" if i == 11 else "hypothesis",
            "text": "q" * 80,
            "support_ids": [f"e{i}"],
        }
        for i in range(12)
    ]
    previous = new_revision("p", None, evidence=evidence, statements=statements)
    projected = project_memory(previous, Tokenizer(), budget=1100)
    assert len(Tokenizer().encode_tokens(serialized({"memory": projected, "evidence": []}))) <= 1100
    assert projected["statements"][0]["id"] == "s11"
    assert len(projected["statements"]) < len(previous.statements)
    revised = apply_patch(
        "p",
        previous,
        {"evidence": [], "statements": [{"id": "new", "kind": "hypothesis", "text": "new"}]},
        set(),
    )
    assert revised.evidence == previous.evidence
    assert revised.statements[:-1] == previous.statements


def test_interaction_missing_targets_stays_pending_and_client_private_scope(tmp_path):
    from tracemind.experiments.interactions import advance_interaction

    cp = {"id": "cp", "current_dialogue": [], "latest_client": {"text": "hello"}}
    candidate = {
        "text": "response",
        "context": {"history": {"memory": "secret history", "evidence": []}},
    }
    queue = Queue()
    assert (
        advance_interaction(tmp_path, queue, cp, "evolution", candidate)["status"]
        == "missing_targets"
    )
    target_dir = tmp_path / "experiments" / "interaction_targets"
    target_dir.mkdir(parents=True)
    (target_dir / "cp.json").write_text(
        json.dumps(
            {
                "target_ids": ["goal"],
                "targets": [{"id": "goal", "criterion": "The client expresses a preference."}],
                "private_current": {"mood": "uncertain"},
            }
        ),
        encoding="utf-8",
    )
    assert advance_interaction(tmp_path, queue, cp, "evolution", candidate)["status"] == "waiting"
    client = next(iter(queue.requests.values()))
    assert client["role"] == "simulated client"
    assert "secret history" not in json.dumps(client["context"])
    assert "target_ids" not in client["context"]


def test_authoritative_evidence_rejects_model_text_for_real_id():
    from datetime import datetime, timezone

    from tracemind.memory.context import canonicalize_evidence_patch
    from tracemind.storage.repository import EvidenceRecord

    now = datetime(2020, 1, 1, tzinfo=timezone.utc)
    record = EvidenceRecord("e1", "case", 1, 1, "client", "actual", now, now)
    import pytest

    with pytest.raises(ValueError):
        canonicalize_evidence_patch(
            {"evidence": [{"id": "e1", "text": "invented", "source": "client"}], "statements": []},
            {"e1": record},
            {"e1"},
            case_id="case",
        )
    patch = canonicalize_evidence_patch(
        {"evidence": ["e1"], "statements": []}, {"e1": record}, {"e1"}, case_id="case"
    )
    assert patch["evidence"][0]["text"] == "actual"
    with pytest.raises(ValueError):
        canonicalize_evidence_patch(
            {"evidence": ["e1"], "statements": []}, {"e1": record}, {"e1"}, case_id="different"
        )


def test_metric_rows_use_both_access_reviews_per_error():
    from tracemind.evaluation.metrics import review_rows, summarize

    errors = {
        k: {"occurred": True}
        for k in ("unsupported_assertion", "leading_question", "missed_progress")
    }
    quality = {
        "mapping": {"a": "evolution", "b": "summary"},
        "result": {
            side: {
                "errors": errors,
                "tentative_exploration": False,
                "scores": {"response_decision": 2},
            }
            for side in ("a", "b")
        },
    }
    flags = {"unsupported_assertion": True, "leading_question": False, "missed_progress": False}
    accessible = {k: False for k in flags}
    rows = review_rows(
        "group",
        "cp",
        quality,
        {
            "a": {"result": {"access_limited_by_error": flags}},
            "b": {"result": {"access_limited_by_error": accessible}},
        },
    )
    result = summarize(rows)
    assert result["denominators"]["unsupported_assertion"] == 1
    assert result["denominators"]["leading_question"] == 2
    assert all(row["group_id"] == "group" for row in rows)
