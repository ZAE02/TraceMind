import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import generator as g
import run_longitudinal as runner


class RevisionTests(unittest.TestCase):
    def test_archive_is_immutable_and_discards_only_replaced_session_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            data={"utterances":[{"id":"a","role":"client","text":"old"},{"id":"b","role":"counselor","text":"old reply"}]}
            g.write_json(root/"dialogues/C01/S02.json",data)
            g.write_json(root/"audit/agent_reviews/C01/S02.json",{"decision":"revise"})
            g.write_json(root/"facts/C01.json",{"confirmed_additions":[{"fact":"prior","first_session":1},{"fact":"replaced","first_session":2}],
                "reported_intentions":[{"first_session":2,"intention":"future plan"}]})
            with patch.object(runner,"ROOT",root):
                self.assertEqual(runner.archive_revision("C01",2,0,keep_prefix=2),data["utterances"])
                first=g.read_json(root/"revisions/C01/S02/r0/S02.json")
                self.assertEqual(first,data)
                ledger=g.read_json(root/"facts/C01.json")
                self.assertEqual(ledger["confirmed_additions"],[{"fact":"prior","first_session":1}])
                self.assertEqual(ledger["reported_intentions"],[])
                g.write_json(root/"dialogues/C01/S02.json",{"utterances":[]})
                runner.archive_revision("C01",2,0)
                self.assertEqual(g.read_json(root/"revisions/C01/S02/r0/S02.json"),first)
                self.assertEqual(len(list((root/"revisions/C01/S02").glob("r0*"))),2)


if __name__=="__main__": unittest.main()
