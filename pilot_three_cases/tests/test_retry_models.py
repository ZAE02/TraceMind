import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generator as g
import retry_models


class FallbackTests(unittest.TestCase):
    def setup_root(self, directory):
        root = Path(directory)
        g.write_json(root / "configs/generation_config.json", {"model": "qwen3.8-27b"})
        g.write_json(root / "audit/configuration_revisions.json", {"revisions": []})
        return root

    def test_primary_success_does_not_switch_models(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.setup_root(directory)
            with patch.object(g, "ROOT", root), patch.object(g, "main", return_value=0) as run, patch("sys.stdout", io.StringIO()):
                self.assertEqual(retry_models.main(), 0)
                self.assertEqual(run.call_count, 1)
                report = g.read_json(root / "audit/api_retry_report.json")
                self.assertEqual(report["model_used"], "qwen3.8-27b")

    def test_two_failures_preserve_empty_attempt_and_record_model_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.setup_root(directory)
            def fail(args):
                g.write_json(root / "dialogues/C01/S01.json", {"utterances": [], "status": "interrupted"})
                raise g.GenerationError("Endpoint.AccessDenied")
            with patch.object(g, "ROOT", root), patch.object(g, "main", side_effect=fail), patch("sys.stdout", io.StringIO()):
                self.assertEqual(retry_models.main(), 1)
                report = g.read_json(root / "audit/api_retry_report.json")
                self.assertEqual([item["model"] for item in report["attempts"]], ["qwen3.8-27b", "deepseek-v4-flash"])
                self.assertEqual(report["status"], "both_models_failed")
                self.assertEqual(len(list((root / "generation_logs/interrupted_attempts").glob("*/C01_S01.json"))), 1)

    def test_partial_real_dialogue_blocks_model_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.setup_root(directory)
            original = {"utterances": [{"role": "client", "text": "真实已生成发言"}], "status": "interrupted"}
            g.write_json(root / "dialogues/C01/S01.json", original)
            with patch.object(g, "ROOT", root):
                with self.assertRaisesRegex(g.GenerationError, "preserve existing real utterances"):
                    retry_models.change_model("deepseek-v4-flash", "test")
                self.assertEqual(g.read_json(root / "dialogues/C01/S01.json"), original)
                self.assertEqual(g.read_json(root / "configs/generation_config.json")["model"], "qwen3.8-27b")


if __name__ == "__main__":
    unittest.main()
