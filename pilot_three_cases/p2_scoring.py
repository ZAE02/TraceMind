"""Validate blind ratings and assemble disagreements, without guessing scores."""
import argparse
import json
import generator as g
from p2_validation import P2, ROOT, read, write


def collect():
    key=read(P2/'internal/ablation_blind_key.json')
    by_reviewer={}
    correction_policy=read(P2/'review_order_risk.json') if (P2/'review_order_risk.json').exists() else {'affected_nodes':{}}
    for reviewer in ('R1','R2'):
        ratings={}
        for path in sorted((P2/'model_ablation_reviews'/reviewer).glob('*.json')):
            if path.stem in correction_policy['affected_nodes'].get(reviewer,[]):
                corrected=P2/'model_ablation_review_corrections'/reviewer/path.name
                if not corrected.exists():raise ValueError('Fresh review correction not yet complete: '+str(corrected))
                path=corrected
            data=read(path)
            if (data.get('reviewer_model')!='gpt-6.1-sol' or data.get('reasoning_effort')!='medium'
                    or data.get('human_review_performed') is not False or data.get('reviewer')!=reviewer):
                raise ValueError('Incorrect rating provenance: '+str(path))
            node=data['node'];public=read(ROOT/'checkpoints/public'/(node+'.json'))
            if data['public_input_sha256']!=g.digest(public):raise ValueError('Rating public hash mismatch')
            expected={id_ for id_,meta in key.items() if meta['node']==node}
            if {item['anonymous_id'] for item in data['items']}!=expected or len(data['items'])!=6:
                raise ValueError('Need exactly six distinct ratings: '+node)
            for item in data['items']:
                for field,high in (('U',4),('R',4),('E',2),('P',1)):
                    value=item.get(field)
                    if isinstance(value,bool) or not isinstance(value,(int,float)) or not 0<=value<=high:
                        raise ValueError('Rating out of range')
                if item['P'] not in (0,1) or not isinstance(item.get('S'),bool):raise ValueError('Invalid error/safety flag')
                id_=item['anonymous_id']
                if id_ in ratings:raise ValueError('Duplicated anonymous rating')
                ratings[id_]=item
        if len(ratings)!=54:raise ValueError('Both complete 54-item reviews required; '+reviewer+' has '+str(len(ratings)))
        by_reviewer[reviewer]=ratings
    disagreement=[]
    for id_ in sorted(key):
        a=by_reviewer['R1'][id_];b=by_reviewer['R2'][id_]
        reasons=[]
        if a['P']!=b['P']:reasons.append('P')
        for field in ('U','R'):
            if abs(a[field]-b[field])>1:reasons.append(field)
        if a['S']!=b['S']:reasons.append('S')
        if reasons:
            node=key[id_]['node']
            packet=read(P2/'blind_ablation/R1'/(node+'.json'))
            answer=next(item for item in packet['answers'] if item['anonymous_id']==id_)
            disagreement.append({'anonymous_id':id_,'node':node,'dimensions':reasons,
                'answer':answer,
                'R1':a,'R2':b})
    write(P2/'adjudication/ablation_disagreements.json',{'kind':'model_adjudication_packet',
        'human_review_performed':False,'condition_labels_hidden':True,'items':disagreement})
    nodes=sorted({item['node'] for item in disagreement})
    for node in nodes:
        packet=read(P2/'blind_ablation/R1'/(node+'.json'))
        write(P2/'adjudication/node_packets'/(node+'.json'),{'node':node,
            'public_input':packet['public_input'],'items':[item for item in disagreement if item['node']==node]})
    write(P2/'adjudication/order.json',{'nodes':nodes,'items':len(disagreement)})
    write(P2/'ratings_validation.json',{'checked_at':g.now(),'status':'complete',
        'independent_reviews':2,'ratings_per_reviewer':54,'disagreements':len(disagreement),
        'P_disagreements':sum('P' in item['dimensions'] for item in disagreement),
        'human_review_performed':False})
    print(json.dumps({'ratings':108,'disagreements':len(disagreement),
        'P_disagreements':sum('P' in item['dimensions'] for item in disagreement)}))


if __name__=='__main__':collect()
