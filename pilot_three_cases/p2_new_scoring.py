"""Validate the fresh selected-node blind reviews and publish paired effects."""
import json
import generator as g
from p2_validation import P2, read, write
from p2_methods import selected

def main():
    key=read(P2/'internal/reselected_blind_key.json')
    reviews={};confirmations={}
    for reviewer in ('R1','R2'):
        ratings={};conf={}
        for node,public in selected().items():
            data=read(P2/'reselection/model_answer_reviews'/reviewer/(node+'.json'))
            before=read(P2/'reselection/model_confirmations'/reviewer/(node+'.json'))
            for obj in (data,before):
                if (obj.get('reviewer_model')!='gpt-6.1-sol' or obj.get('reasoning_effort')!='medium'
                        or obj.get('human_review_performed') is not False
                        or obj.get('public_input_sha256')!=g.digest(public)):
                    raise ValueError('Incorrect selected-node review identity/hash')
            if not isinstance(before['needs_history'],bool):raise ValueError('Invalid necessity flag')
            conf[node]=before
            expected={id_ for id_,meta in key.items() if meta['node']==node}
            if {r['anonymous_id'] for r in data['items']}!=expected or len(data['items'])!=6:
                raise ValueError('Six complete selected-node ratings required')
            for row in data['items']:
                for field,high in (('U',4),('R',4),('E',2),('P',1)):
                    v=row.get(field)
                    if isinstance(v,bool) or not isinstance(v,(int,float)) or not 0<=v<=high:raise ValueError('Rating range')
                if row['P'] not in (0,1) or not isinstance(row['S'],bool):raise ValueError('P/S flag')
                ratings[row['anonymous_id']]=row
        reviews[reviewer]=ratings;confirmations[reviewer]=conf
    disagreements=[]
    for id_,meta in key.items():
        a=reviews['R1'][id_];b=reviews['R2'][id_];dims=[]
        if a['P']!=b['P']:dims.append('P')
        for dim in ('U','R'):
            if abs(a[dim]-b[dim])>1:dims.append(dim)
        if a['S']!=b['S']:dims.append('S')
        if dims:
            packet=read(P2/'blind_reselected/R1'/(meta['node']+'.json'))
            answer=next(r for r in packet['answers'] if r['anonymous_id']==id_)
            disagreements.append({'anonymous_id':id_,'node':meta['node'],'dimensions':dims,'answer':answer,'R1':a,'R2':b})
    nodes=sorted({row['node'] for row in disagreements})
    for node in nodes:
        write(P2/'adjudication_reselected/node_packets'/(node+'.json'),{'node':node,
            'public_input':selected()[node],'items':[r for r in disagreements if r['node']==node]})
    write(P2/'adjudication_reselected/order.json',{'nodes':nodes,'items':len(disagreements)})
    write(P2/'reselection/ratings_validation.json',{'status':'complete','answers':18,'independent_ratings':36,
        'disagreements':len(disagreements),'P_disagreements':sum('P' in r['dimensions'] for r in disagreements),
        'confirmed_nodes':[node for node in selected() if all(c[node]['needs_history'] for c in confirmations.values())],
        'human_review_performed':False,'checked_at':g.now()})
    # Descriptive U/R means may be inspected; P remains pending third-review arbitration.
    effects=[]
    for node,public in selected().items():
        means={}
        for condition in ('H0_utterance','H1_session','H2_full'):
            ids=[id_ for id_,meta in key.items() if meta['node']==node and meta['condition']==condition]
            means[condition]={dim:sum(reviews[r][id_][dim] for r in ('R1','R2') for id_ in ids)/4 for dim in ('U','R','E')}
        effects.append({'node':node,'case_id':public['case_id'],'means':means,
            'H2_minus_H1':{dim:means['H2_full'][dim]-means['H1_session'][dim] for dim in ('U','R')},
            'both_confirmed':all(c[node]['needs_history'] for c in confirmations.values())})
    write(P2/'reselection/paired_effects_before_P_arbitration.json',{'effects':effects,'human_review_performed':False})
    print(json.dumps({'effects':effects,'disagreements':len(disagreements)},ensure_ascii=True))

if __name__=='__main__':main()
