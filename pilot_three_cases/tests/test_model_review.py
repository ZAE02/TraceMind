import json
import sys
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generator as g


class ModelReviewTests(unittest.TestCase):
    def fixture(self, root):
        rows = [{"id":"C01-S01-U%02d"%(n+1), "role":g.ROLES[n%2], "text":"实际发言"} for n in range(16)]
        g.write_json(root / "dialogues/C01/S01.json", {"utterances":rows,"status":"awaiting_model_review"})
        review = {"kind":"model_review", "reviewer_model":"gpt-6.1-sol", "reasoning_effort":"high",
                  "case_id":"C01","session_number":1,
                  "transcript_sha256":g.digest(g.visible_utterances(rows)), "decision":"approve", "major_issues":[],
                  "scores":{"naturalness":4,"counseling_quality":4,"longitudinal_plausibility":4}}
        g.write_json(root / "audit/agent_reviews/C01/S01.json", review)
        return review

    def test_requested_model_identity_and_transcript_hash_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); review = self.fixture(root)
            self.assertEqual(g.validate_model_review(root,"C01",1)["decision"],"approve")
            review["reviewer_model"] = "qwen3.8-27b"
            g.write_json(root / "audit/agent_reviews/C01/S01.json", review)
            with self.assertRaises(g.GenerationError): g.validate_model_review(root,"C01",1)
            review["reviewer_model"] = "gpt-6.1-sol"; review["transcript_sha256"] = "old"
            g.write_json(root / "audit/agent_reviews/C01/S01.json", review)
            with self.assertRaises(g.GenerationError): g.validate_model_review(root,"C01",1)

    def test_rejected_or_low_score_review_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); review = self.fixture(root)
            review["scores"]["counseling_quality"] = 3
            g.write_json(root / "audit/agent_reviews/C01/S01.json", review)
            with self.assertRaises(g.GenerationError): g.validate_model_review(root,"C01",1)

    def test_concurrent_large_logs_do_not_interleave(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with ThreadPoolExecutor(max_workers=3) as executor:
                list(executor.map(lambda n:g.append_log(root,{"id":n,"body":"自然对话"*8000}),range(30)))
            records=[json.loads(line) for line in (root/"generation_logs/full_agent_calls.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records),30)
            self.assertEqual({r["id"] for r in records},set(range(30)))

    def test_incomplete_dialogue_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); self.fixture(root)
            data=g.read_json(root/"dialogues/C01/S01.json"); data["status"]="generating"
            g.write_json(root/"dialogues/C01/S01.json",data)
            with self.assertRaises(g.GenerationError): g.validate_model_review(root,"C01",1)

    def test_relaxed_review_requires_explicit_policy_and_keeps_major_issue_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); review = self.fixture(root)
            review.update(reviewer_model='gpt-6-luna', reasoning_effort='low')
            review['scores'] = dict.fromkeys(review['scores'], 3)
            path = root / 'audit/agent_reviews/C01/S01.json'
            g.write_json(path, review)
            with self.assertRaises(g.GenerationError): g.validate_model_review(root, 'C01', 1)
            g.write_json(root / 'configs/review_policy.json', {'mode': 'user_authorized_model_review',
                'human_review_claim': False, 'reviewer_model': 'gpt-6-luna', 'reasoning_effort': 'low'})
            self.assertEqual(g.validate_model_review(root, 'C01', 1)['decision'], 'approve')
            review['major_issues'] = ['material contradiction']; g.write_json(path, review)
            with self.assertRaises(g.GenerationError): g.validate_model_review(root, 'C01', 1)


if __name__ == "__main__": unittest.main()
