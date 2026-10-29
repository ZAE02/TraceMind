import copy
import unittest
from unittest.mock import patch
import p2_methods as p

class P2MethodTests(unittest.TestCase):
    def test_memory_budget_does_not_edit_the_raw_state(self):
        raw={'entries':[{'text':'很长的经历'*300,'evidence_ids':['C01-S01-U01']} for _ in range(10)]}
        original=copy.deepcopy(raw)
        state,dropped=p.fit_memory(raw)
        self.assertLessEqual(p.tokens(state),3000)
        self.assertEqual(raw,original)
        self.assertTrue(dropped)
        self.assertEqual(len(state['entries'])+len(dropped),10)

    def test_retrieval_keeps_speaker_time_and_only_precheckpoint_sources(self):
        public={'current_session':3,'previous_session_dialogues':[
            {'session_number':1,'utterances':[{'id':'C01-S01-U01','role':'client','text':'担心工作评价'},
                                            {'id':'C01-S01-U02','role':'counselor','text':'是否还担心？'}]},
            {'session_number':2,'utterances':[{'id':'C01-S02-U01','role':'client','text':'工作评价不同了'},
                                            {'id':'C01-S02-U02','role':'counselor','text':'什么不同？'}]}],
            'current_session_dialogue':[], 'current_user_utterance':{'id':'C01-S03-U01','role':'client','text':'工作评价'}}
        with patch.object(p,'embedding',side_effect=lambda secret,texts:[[1.,0.] for _ in texts]):
            memory,info=p.retrieve('fake',public)
        self.assertLessEqual(info['history_tokens'],3000)
        self.assertEqual([d['session_number'] for d in memory],[1,2])
        self.assertEqual([r['role'] for d in memory for r in d['utterances']],['client','counselor','client','counselor'])
        self.assertNotIn('C01-S03-U01',info['selected_ids'])

if __name__=='__main__':unittest.main()
