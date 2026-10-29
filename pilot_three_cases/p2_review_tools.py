"""Independent node gate and anonymous, paired P2 answer packets."""
import argparse
import csv
import json
import random
from pathlib import Path
import generator as g
from p2_validation import P2, ROOT, read, write


def visible_ids(public):
    rows = [r for s in public['previous_session_dialogues'] for r in s['utterances']]
    rows += public['current_session_dialogue'] + [public['current_user_utterance']]
    return {r['id'] for r in rows}


def node_gate():
    manifest = read(P2 / 'frozen_manifest.json')
    reviews = []
    for reviewer in ('R1','R2'):
        review = read(P2 / 'model_node_reviews' / (reviewer + '.json'))
        if (review.get('reviewer_model') != 'gpt-6.1-sol' or review.get('reasoning_effort') != 'medium'
                or review.get('human_review_performed') is not False or review.get('reviewer') != reviewer):
            raise ValueError('Unexpected reviewer identity: ' + reviewer)
        mapping = {item['node']:item for item in review['nodes']}
        if len(mapping) != 9 or set(mapping) != set(manifest['nodes']):
            raise ValueError('Nine distinct reviews required')
        for node, item in mapping.items():
            public = read(ROOT / 'checkpoints/public' / (node + '.json'))
            if (item.get('public_input_sha256') != g.digest(public)
                    or not isinstance(item.get('needs_history'), bool)
                    or not set(item.get('evidence_ids', [])) <= visible_ids(public)):
                raise ValueError('Stale or invalid node review: ' + reviewer + ' ' + node)
        reviews.append(mapping)
    accepted = [node for node in manifest['nodes'] if all(r[node]['needs_history'] for r in reviews)]
    cases = sorted({node.split('_')[0] for node in accepted})
    disagreements = [node for node in manifest['nodes'] if reviews[0][node]['needs_history'] != reviews[1][node]['needs_history']]
    result = {'kind':'two_independent_model_gate','human_review_performed':False,
        'reviewer_model':'gpt-6.1-sol','reasoning_effort':'medium',
        'accepted_nodes':accepted,'accepted_cases':cases,'disagreements':disagreements,
        'passed':len(accepted)>=3 and len(cases)>=2,'checked_at':g.now(),
        'interpretation':'Provisional model-assisted gate. Not human or clinical validation.'}
    write(P2 / 'node_gate.json', result)
    fields = ['node','R1_needs_history','R2_needs_history','both_confirmed','R1_reason','R2_reason',
              'R1_cue_leakage','R2_cue_leakage']
    with (P2 / 'node_audit_model.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for node in manifest['nodes']:
            a,b=(r[node] for r in reviews)
            w.writerow({'node':node,'R1_needs_history':a['needs_history'],'R2_needs_history':b['needs_history'],
                'both_confirmed':node in accepted,'R1_reason':a['reason'],'R2_reason':b['reason'],
                'R1_cue_leakage':a['cue_leakage'],'R2_cue_leakage':b['cue_leakage']})
    print(json.dumps(result,ensure_ascii=True))


def make_blind():
    answers = [json.loads(line) for line in (P2 / 'history_ablation.jsonl').read_text(encoding='utf-8').splitlines()]
    keys={(r['node'],r['condition'],r['sample']) for r in answers}
    if len(answers)!=54 or len(keys)!=54:
        raise ValueError('Complete 54 distinct answers required')
    public_map={p.stem:read(p) for p in (ROOT/'checkpoints/public').glob('*.json')}
    anonymous={}
    for index, row in enumerate(sorted(answers,key=lambda r:(r['node'],r['condition'],r['sample']))):
        id_='A'+g.digest({'node':row['node'],'condition':row['condition'],'sample':row['sample']})[:12]
        anonymous[id_]={'node':row['node'],'condition':row['condition'],'sample':row['sample'],
                        'model_returned':row['model_returned'],'call_id':row['call_id']}
    private=P2/'internal';private.mkdir(exist_ok=True)
    write(private/'ablation_blind_key.json',anonymous)
    by_key={(r['node'],r['condition'],r['sample']):r for r in answers}
    for reviewer,seed in (('R1',7319),('R2',8243)):
        out=P2/'blind_ablation'/reviewer;out.mkdir(parents=True,exist_ok=True)
        rng=random.Random(seed)
        nodes=sorted(public_map);rng.shuffle(nodes)
        write(out/'order.json',{'node_order':nodes,'anonymous_groups':True,'human_review_performed':False})
        for node in nodes:
            items=[]
            for id_,meta in anonymous.items():
                if meta['node']!=node:continue
                row=by_key[(node,meta['condition'],meta['sample'])]
                items.append({'anonymous_id':id_,'answer':row['parsed_answer'],
                    'raw_text':row['raw_text'],'visible_evidence_ids':row['visible_evidence_ids'],
                    'format_valid':row['format_valid']})
            rng.shuffle(items)
            write(out/(node+'.json'),{'node':node,'public_input':public_map[node],'answers':items,
                'note':'Method/condition/model names hidden. Available-evidence IDs support fair citation checking; their counts can reveal context size.'})
    print('Two independently shuffled packets generated: 54 answers each. Condition key stored separately.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=('gate','blind'))
    args=parser.parse_args()
    (node_gate if args.phase=='gate' else make_blind)()

if __name__=='__main__':main()
