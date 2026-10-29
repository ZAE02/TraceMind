import json
import unittest
import p2_validation as p2
import generator as g


class P2BoundaryTests(unittest.TestCase):
    def test_conditions_only_deliver_their_declared_evidence(self):
        public = {'session_background': {'current_relative_week': 3},
            'previous_session_dialogues': [{'session_number':1,'utterances':[{'id':'C-S01-U01','role':'client','text':'old'}]}],
            'current_session_dialogue':[{'id':'C-S03-U01','role':'client','text':'current prefix'}],
            'current_user_utterance':{'id':'C-S03-U03','role':'client','text':'latest'},
            'private_state':'SECRET', 'annotation':'GOLD', 'future_event':'FUTURE'}
        h0 = p2.condition_input(public, 'H0_utterance')
        h1 = p2.condition_input(public, 'H1_session')
        h2 = p2.condition_input(public, 'H2_full')
        self.assertEqual(h0['previous_session_dialogues'], [])
        self.assertEqual(h0['current_session_dialogue'], [])
        self.assertEqual(h1['previous_session_dialogues'], [])
        self.assertEqual(h1['current_session_dialogue'], public['current_session_dialogue'])
        self.assertEqual(h2['previous_session_dialogues'], public['previous_session_dialogues'])
        for condition in (h0,h1,h2):
            self.assertEqual(condition['current_user_utterance'], public['current_user_utterance'])
            self.assertNotIn('SECRET',json.dumps(condition))
            self.assertNotIn('GOLD',json.dumps(condition))
            self.assertNotIn('FUTURE',json.dumps(condition))

    def test_p2_payload_has_same_task_and_no_generation_role_directive(self):
        config=g.read_json(g.ROOT/'configs/generation_config.json')
        context={'session_background':{},'previous_session_dialogues':[],
                 'current_session_dialogue':[], 'current_user_utterance':{'id':'X','role':'client','text':'hello'}}
        payload=g.build_payload('counselor',p2.PROMPT,dict(config,role_reasoning_effort={}),context)
        self.assertEqual(payload['input'][0]['content'],p2.PROMPT)
        self.assertEqual(len(payload['input']),2)
        self.assertIs(payload['store'],False)
        self.assertIs(payload['stream'],False)
        self.assertNotIn('previous_response_id',payload)


if __name__=='__main__': unittest.main()
