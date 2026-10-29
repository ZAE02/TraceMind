import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib import error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generator as g
from prepare import plans


class FakeResponse:
    status = 200
    headers = {"x-request-id": "request-test"}

    def __init__(self, text="我想从这周的事说起。", status="completed"):
        self.body = {"id": "response-test", "model": "qwen3.8-27b", "status": status,
                     "output": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "PRIVATE_REASONING"}]},
                                {"type": "message", "role": "assistant", "status": "completed",
                                 "content": [{"type": "output_text", "text": text}]}]}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.body, ensure_ascii=False).encode("utf-8")


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.plan = plans()[2]
        self.plan["research_only"] = "GOLD_CANARY"
        self.plan["session_cards"][7]["private_experiences"].append("FUTURE_CANARY")
        self.current = [{"id": "C03-S01-U01", "role": "client", "text": "我卡了两天，还没有找人。"}]
        self.ledger = {"stable_profile": {"private": "LEDGER_CANARY"}, "confirmed_additions": [], "research_only": "LEDGER_CANARY"}

    def test_counselor_allowlist_and_latest_public_utterance(self):
        context = g.context_for("counselor", self.plan, 1, [], self.current, self.ledger)
        serialized = json.dumps(context, ensure_ascii=False)
        self.assertEqual(set(context), {"session", "previous_session_dialogues", "current_session_dialogue", "time_notice"})
        self.assertIn("我卡了两天", serialized)
        for value in ("GOLD_CANARY", "FUTURE_CANARY", "LEDGER_CANARY", "private_experiences", "client_profile"):
            self.assertNotIn(value, serialized)

    def test_client_does_not_receive_research_or_future(self):
        context = g.context_for("client", self.plan, 1, [], [], self.ledger)
        serialized = json.dumps(context, ensure_ascii=False)
        self.assertIn("private_experiences", serialized)
        self.assertEqual(context["occurred_life_events"], [])
        for value in ("GOLD_CANARY", "FUTURE_CANARY", "LEDGER_CANARY", "session_cards"):
            self.assertNotIn(value, serialized)

    def test_only_past_events_at_later_session(self):
        context = g.context_for("client", self.plan, 5, [], [], self.ledger)
        self.assertEqual([event["session_number"] for event in context["occurred_life_events"]], [1, 2, 3, 4])

    def test_confirmed_facts_are_only_from_prior_sessions(self):
        ledger = {"confirmed_additions": [{"first_session": 1, "fact": "earlier fact"},
                                         {"first_session": 2, "fact": "current-session fact"}]}
        context = g.context_for("client", self.plan, 2, [], [], ledger)
        self.assertEqual(context["confirmed_additional_facts"], [{"fact": "earlier fact"}])

    def test_profile_and_card_count(self):
        for plan in plans():
            self.assertEqual(len(plan["session_cards"]), 8)
            self.assertEqual([item["relative_week"] for item in plan["session_cards"]], list(range(1, 9)))


class ResponseTests(unittest.TestCase):
    def test_reasoning_is_not_a_dialogue(self):
        self.assertEqual(g.extract_text(FakeResponse().body), "我想从这周的事说起。")

    def test_reject_truncation_empty_and_two_roles(self):
        for response in (FakeResponse(status="incomplete").body, FakeResponse(text="").body,
                         FakeResponse(text="来访者：我累了。\n咨询师：什么事？").body):
            with self.assertRaises(g.GenerationError):
                g.extract_text(response)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.config = {"base_url": "https://example.invalid/v1", "max_retries": 2, "request_timeout_seconds": 120}
        self.payload = {"model": "qwen3.8-27b", "input": []}

    def test_transient_retry_and_log_redaction(self):
        calls = []
        delays = []
        def transport(req, timeout):
            calls.append(req)
            if len(calls) == 1:
                raise error.HTTPError(req.full_url, 429, "limited", {}, io.BytesIO(b"test-secret"))
            return FakeResponse()
        with tempfile.TemporaryDirectory() as directory:
            text, call_id, response = g.call_api(Path(directory), self.config, self.payload, {}, "test-secret", transport, delays.append)
            records_text = (Path(directory) / "generation_logs/full_agent_calls.jsonl").read_text(encoding="utf-8")
            records = [json.loads(line) for line in records_text.splitlines()]
            self.assertEqual(len(calls), 2)
            self.assertEqual(delays, [2])
            self.assertNotIn("test-secret", records_text)
            self.assertEqual(records[0]["status"], "http_error")
            self.assertEqual(records[1]["retry_count"], 1)
            self.assertEqual(records[0]["logical_call_id"], records[1]["logical_call_id"])

    def test_authentication_error_is_not_retried(self):
        calls = []
        def transport(req, timeout):
            calls.append(req)
            raise error.HTTPError(req.full_url, 401, "auth", {}, io.BytesIO(b"invalid"))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(g.GenerationError):
                g.call_api(Path(directory), self.config, self.payload, {}, "secret", transport, lambda _: self.fail("must not sleep"))
            self.assertEqual(len(calls), 1)

    def test_only_two_retries(self):
        calls = []
        def transport(req, timeout):
            calls.append(req)
            raise error.URLError("timeout")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(g.GenerationError):
                g.call_api(Path(directory), self.config, self.payload, {}, "secret", transport, lambda _: None)
            self.assertEqual(len(calls), 3)

    def test_recover_successful_log_without_new_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = {"status": "success", "case_id": "C03", "session_number": 1, "utterance_id": "C03-S01-U01",
                     "config_hash": "hash1", "request": self.payload, "spoken_text": "你好", "logical_call_id": "saved", "response": {}}
            g.append_log(root, entry)
            recovered = g.recover_logged_call(root, "C03", 1, "C03-S01-U01", self.payload, "hash1")
            self.assertEqual(recovered["logical_call_id"], "saved")
            self.assertIsNone(g.recover_logged_call(root, "C03", 1, "C03-S01-U01", self.payload, "changed"))


class ReviewTests(unittest.TestCase):
    def test_no_model_or_blank_score_can_pass_human_gate(self):
        review = {"reviewers": [{"kind": "model", "reviewer_id": "m1", "major_issues": [],
                                 "scores": {"naturalness": 5, "counseling_quality": 5, "longitudinal_plausibility": 5}}]}
        self.assertFalse(g.human_review_passes(review))
        self.assertFalse(g.human_review_passes({"reviewers": []}))

    def test_two_distinct_human_reviews_required(self):
        review = {"reviewers": [{"kind": "human", "reviewer_id": reviewer, "major_issues": [],
                                 "scores": {"naturalness": 4, "counseling_quality": 4, "longitudinal_plausibility": 4}}
                                for reviewer in ("human-1", "human-2")]}
        self.assertTrue(g.human_review_passes(review))
        review["reviewers"][1]["reviewer_id"] = "human-1"
        self.assertFalse(g.human_review_passes(review))
        review["reviewers"][1]["reviewer_id"] = "human-2"
        review["reviewers"][0]["major_issues"] = ["Gold leakage"]
        self.assertFalse(g.human_review_passes(review))

    def test_role_order_and_complete_pair_validation(self):
        rows = [{"id": str(index), "role": g.ROLES[index % 2], "text": "发言"} for index in range(20)]
        self.assertEqual(g.validate_transcript({"utterances": rows}), [])
        rows[2]["role"] = "counselor"
        self.assertTrue(g.validate_transcript({"utterances": rows}))

    def test_closing_requires_both_roles_and_minimum_pairs(self):
        rows = [{"id": str(index), "role": g.ROLES[index % 2], "text": "继续讨论。"} for index in range(20)]
        rows[-1]["text"] = "今天就先到这里，下次见。"
        self.assertFalse(g.mutual_closing(rows))
        rows[-2]["text"] = "嗯，今天先这样，下次见。"
        self.assertTrue(g.mutual_closing(rows))
        self.assertFalse(g.mutual_closing(rows[-2:]))


class PersistenceTests(unittest.TestCase):
    def test_session_two_gate_prevents_any_api_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            g.write_json(root / "configs/generation_config.json", {"api_key_env": "DASHSCOPE_API_KEY"})
            g.write_json(root / "audit/quality_scores.json", {"sessions": []})
            for role in g.ROLES:
                (root / "configs" / (role + "_system_prompt.txt")).write_text("role prompt", encoding="utf-8")
            with patch.object(g, "ROOT", root), patch.object(g, "preflight", return_value={"status": "passed"}), \
                    patch.dict(g.os.environ, {"DASHSCOPE_API_KEY": "fake-key"}), patch.object(g.request, "urlopen") as transport:
                with self.assertRaisesRegex(g.GenerationError, "S1 human review gate"):
                    g.main(["--phase", "session", "--case", "C01", "--session", "2"])
                transport.assert_not_called()

    def test_interrupted_session_resumes_without_repeating_committed_turns(self):
        config = {"base_url": "https://example.invalid/v1", "model": "test-model", "temperature": 0.7,
                  "reasoning_effort": "low", "max_output_tokens": 4096, "max_retries": 2,
                  "request_timeout_seconds": 120, "min_pairs": 8, "max_pairs": 12, "prompt_version": "test"}
        plan = plans()[0]
        prompts = {"client": "client prompt", "counselor": "counselor prompt"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            g.write_json(root / "facts/C01.json", {"confirmed_additions": [], "pending_dialogue_reviews": []})
            calls = []
            fail_once = [True]
            def transport(req, timeout):
                context = json.loads(json.loads(req.data.decode("utf-8"))["input"][1]["content"])
                prefix_length = len(context["current_session_dialogue"])
                calls.append(prefix_length)
                if prefix_length == 2 and fail_once[0]:
                    fail_once[0] = False
                    raise error.HTTPError(req.full_url, 403, "denied", {}, io.BytesIO(b"denied"))
                return FakeResponse("今天先这样，下次见。" if prefix_length >= 18 else "这周还在想那件事。")
            with patch.object(g.request, "urlopen", transport), patch("builtins.print"):
                with self.assertRaises(g.GenerationError):
                    g.generate_session(root, config, plan, 1, prompts, "fake-key")
                saved = g.read_json(root / "dialogues/C01/S01.json")
                self.assertEqual(len(saved["utterances"]), 2)
                self.assertEqual(saved["status"], "interrupted")
                g.generate_session(root, config, plan, 1, prompts, "fake-key")
                finished = g.read_json(root / "dialogues/C01/S01.json")
                self.assertEqual(len(finished["utterances"]), 20)
                self.assertEqual(finished["status"], "awaiting_human_review")
                self.assertEqual(finished["utterances"][:2], saved["utterances"])
                self.assertEqual(calls.count(0), 1)
                self.assertEqual(calls.count(1), 1)
                ledger = g.read_json(root / "facts/C01.json")
                ledger["confirmed_additions"] = [{"first_session": 1, "fact": "This session was subsequently reviewed."}]
                g.write_json(root / "facts/C01.json", ledger)
                before = len(calls)
                g.generate_session(root, config, plan, 1, prompts, "fake-key")
                self.assertEqual(len(calls), before)
                self.assertEqual(g.read_json(root / "facts/C01.json")["pending_dialogue_reviews"], [1])

    def test_runner_lock_blocks_duplicate_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with g.exclusive_run(root):
                with self.assertRaises(g.GenerationError):
                    with g.exclusive_run(root):
                        self.fail("second process must not enter")
            self.assertFalse((root / "generation_logs/runner.lock").exists())


if __name__ == "__main__":
    unittest.main()
