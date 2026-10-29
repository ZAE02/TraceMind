import unittest
from export_checkpoints import evidence_ids


class EvidenceTests(unittest.TestCase):
    def test_collects_nested_support_and_direct_reference(self):
        annotation = {"supported_understanding": [{"claim": "bounded", "evidence": ["C01-S02-U01"]}],
                      "relevant_evidence": [{"utterance_id": "C01-S01-U03", "why": "past"}]}
        self.assertEqual(evidence_ids(annotation), ["C01-S02-U01", "C01-S01-U03"])

    def test_rejects_unstructured_evidence_string(self):
        import generator as g
        with self.assertRaises(g.GenerationError):
            evidence_ids({"evidence": "C01-S02-U01"})
