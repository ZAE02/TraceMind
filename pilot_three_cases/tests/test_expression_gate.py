import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import json
import generator as g


class ExpressionGateTests(unittest.TestCase):
    def test_flags_quote_and_premature_close(self):
        self.assertTrue(g.expression_issues('你说"烦"。注意到就够了。'))
        self.assertEqual(g.expression_issues('听着这周自己的安排一再被打乱，挺累的。你当时还考虑过哪些选择？'), [])

    def test_brief_acknowledgment_ends_explicit_goodbye(self):
        rows = [{"text": "content"}] * 14 + [{"text": "那下次见。"}, {"text": "好，今天先带着这个疑问。"}]
        self.assertTrue(g.mutual_closing(rows))
        rows[-1] = {"text": "那你明天准备怎么做？"}
        self.assertFalse(g.mutual_closing(rows))

    def test_client_receives_final_api_utterance_and_unsent_candidate_is_retained(self):
        plan = g.read_json(g.ROOT / "configs/case_plans/C01.json")
        config = dict(g.read_json(g.ROOT / "configs/generation_config.json"),
                      expression_gate=True, min_pairs=8, target_pairs=8, max_pairs=8)
        calls = []
        good = "这件事让你挺累的。你当时怎样决定继续处理的？"
        def fake_api(root, active, payload, context, secret):
            calls.append((payload, context))
            text = (good if context.get("candidate_revision") else '你说"烦"。') if context["role"] == "counselor" else "这周事情很多，我还没想明白。"
            call_id = "test-%d" % len(calls)
            response = {"id": call_id, "model": active["model"], "status": "completed"}
            g.append_log(root, {**context, "logical_call_id": call_id, "status": "success", "request": payload,
                                "spoken_text": text, "response": response})
            return text, call_id, response
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            g.write_json(root / "facts/C01.json", {"confirmed_additions": [], "pending_dialogue_reviews": []})
            with patch.object(g, "call_api", fake_api), patch("builtins.print"):
                g.generate_session(root, config, plan, 1, {"client": "client", "counselor": "counselor"}, "dummy")
            rows = g.read_json(root / "dialogues/C01/S01.json")["utterances"]
            self.assertEqual(rows[1]["text"], good)
            second_client_payload = [p for p,c in calls if c["role"] == "client"][1]
            visible = json.loads(second_client_payload["input"][1]["content"])["current_session_dialogue"]
            self.assertEqual(visible[-1]["text"], good)
            logs = (root / "generation_logs/full_agent_calls.jsonl").read_text(encoding="utf-8")
            self.assertIn('quotation', json.dumps(g.expression_issues('你说"烦"。')))
            self.assertIn('candidate_revision', logs)
            self.assertEqual(len(calls), 24)
