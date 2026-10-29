"""Freeze natural replacement candidates without changing the original nine nodes."""
import json
import generator as g
from p2_validation import P2, ROOT, read, write, expected_public

def main():
    proposal=read(P2/'reselection/candidates.json')
    originals=read(P2/'frozen_manifest.json')['nodes']
    original_ids={item['current_utterance_id'] for item in originals.values()}
    manifest={}
    for item in proposal['candidates']:
        id_=item['candidate_id'];cid=item['case_id'];number=item['current_session']
        if item['freeze_utterance_id'] in original_ids:raise ValueError('Not a new cut')
        previous,prefix,latest=expected_public(cid,number,item['freeze_utterance_id'])
        valid={row['id'] for s in previous for row in s['utterances']}
        valid.update(row['id'] for row in prefix+[latest])
        if not set(item['evidence_ids'])<=valid:raise ValueError('Future selection evidence')
        public={'schema_version':'1.0','synthetic':True,'case_id':cid,'checkpoint_id':id_,
                'current_session':number,'session_background':{'current_relative_week':number,
                    'typical_interval_days':7,'format':'同一来访者与咨询师的连续个人咨询'},
                'previous_session_dialogues':previous,'current_session_dialogue':prefix,
                'current_user_utterance':latest}
        destination=P2/'reselection/public'/(id_+'.json')
        if destination.exists() and g.digest(read(destination))!=g.digest(public):
            raise ValueError('Previously frozen candidate changed')
        write(destination,public)
        manifest[id_]={'case_id':cid,'session':number,'current_utterance_id':latest['id'],
                       'public_input_sha256':g.digest(public),'selection':'exploratory; actual dialogue unchanged'}
    write(P2/'reselection/frozen_manifest.json',{'synthetic':True,'created_at':g.now(),'nodes':manifest,
        'notice':'Selected after original node gate failed. No new cases or edited utterances. Candidate selection saw later dialogue; independent raters receive only frozen public evidence.'})
    print('Frozen %d replacement candidates' % len(manifest))

def gate():
    manifest=read(P2/'reselection/frozen_manifest.json')['nodes']
    reviews=[]
    for reviewer in ('R1','R2'):
        data=read(P2/'reselection/model_reviews'/(reviewer+'.json'))
        if (data.get('reviewer_model')!='gpt-6.1-sol' or data.get('reasoning_effort')!='medium'
                or data.get('human_review_performed') is not False):raise ValueError('Reviewer identity')
        rows={row['node']:row for row in data['nodes']}
        if set(rows)!=set(manifest) or len(data['nodes'])!=len(manifest):raise ValueError('Incomplete candidate review')
        for id_,row in rows.items():
            public=read(P2/'reselection/public'/(id_+'.json'))
            available={r['id'] for s in public['previous_session_dialogues'] for r in s['utterances']}
            available.update(r['id'] for r in public['current_session_dialogue']+[public['current_user_utterance']])
            if (row['public_input_sha256']!=g.digest(public) or not isinstance(row['needs_history'],bool)
                    or not set(row['evidence_ids'])<=available):raise ValueError('Candidate review boundary/hash')
        reviews.append(rows)
    accepted=[id_ for id_ in manifest if all(r[id_]['needs_history'] for r in reviews)]
    cases=sorted({manifest[id_]['case_id'] for id_ in accepted})
    passed=len(accepted)>=3 and len(cases)>=2
    selected=[]
    if passed:
        # Predetermined coverage rule: one latest accepted cut per case, then ID order to reach 3.
        for case in cases:
            options=[id_ for id_ in accepted if manifest[id_]['case_id']==case]
            selected.append(max(options,key=lambda id_:(manifest[id_]['session'],id_)))
        selected=selected[:3]
        selected += [id_ for id_ in accepted if id_ not in selected][:3-len(selected)]
    write(P2/'reselection/node_gate.json',{'kind':'two_independent_model_gate',
        'human_review_performed':False,'reviewer_model':'gpt-6.1-sol','reasoning_effort':'medium',
        'accepted_nodes':accepted,'accepted_cases':cases,'passed':passed,'selected_nodes':selected,
        'selection_rule':'At most 3: latest accepted session per case; fill remaining slots in candidate-ID order.',
        'interpretation':'Exploratory re-cut and model-assisted validation; original nine-node negative gate retained.',
        'checked_at':g.now()})
    print(json.dumps({'accepted':accepted,'cases':cases,'passed':passed,'selected':selected}))

if __name__=='__main__':
    import sys
    (gate if 'gate' in sys.argv else main)()
