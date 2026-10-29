"""Anonymous probe/branch packets and validated paired model review collection."""
import argparse
import collections
import csv
import random
import generator as g
from p2_validation import P2,read,write
from p2_methods import selected

def publish(kind):
    key={};packets={}
    for node in ('N04','N06'):
        items=[]
        if kind=='probe':
            for condition in ('M0','M_old','M_updated'):
                for sample in (1,2):
                    path=P2/'probe_answers'/('%s_%s_%d.json'%(node,condition,sample))
                    if not path.exists():continue
                    row=read(path);id_='Z'+g.digest({'node':node,'condition':condition,'sample':sample})[:12]
                    key[id_]={'node':node,'condition':condition,'sample':sample,'source':str(path.relative_to(P2))}
                    items.append({'anonymous_id':id_,'answer':row['parsed_answer'],'raw_text':row['raw_text'],
                        'visible_evidence_ids':row['visible_evidence_ids'],'format_valid':isinstance(row['parsed_answer'],dict)})
        else:
            for path in sorted((P2/'interactive_branches').glob('*/dialogue.json')):
                row=read(path)
                if row['node']!=node:continue
                id_='T'+g.digest({'branch':row['branch_id']})[:12]
                key[id_]={'node':node,'method':row['method'],'replicate':row['replicate'],'source':str(path.relative_to(P2))}
                items.append({'anonymous_id':id_,'utterances':[{'id':r['id'],'role':r['role'],'text':r['text']} for r in row['utterances']]})
        # Publish only complete nodes, to prevent partial-group review.
        if len(items)!=6:
            key={k:v for k,v in key.items() if v['node']!=node};continue
        packets[node]={'node':node,'public_input':selected()[node],'answers' if kind=='probe' else 'branches':items}
    write(P2/'internal'/(kind+'_blind_key.json'),key)
    for reviewer,seed in (('R1',8101),('R2',8102)):
        rng=random.Random(seed)
        write(P2/('blind_'+kind)/reviewer/'order.json',{'nodes':['N06','N04'] if reviewer=='R1' else ['N04','N06'],'expected_items':12})
        for node,packet in packets.items():
            field='answers' if kind=='probe' else 'branches'
            value=dict(packet);value[field]=list(packet[field]);rng.shuffle(value[field])
            write(P2/('blind_'+kind)/reviewer/(node+'.json'),value)
    print({'kind':kind,'ready_nodes':list(packets),'items':len(key)})

def collect(kind):
    key=read(P2/'internal'/('main_blind_key.json' if kind=='main' else kind+'_blind_key.json'))
    target=24 if kind=='main' else 12
    if len(key)!=target:raise ValueError('Incomplete packets')
    reviews={};disputes=[]
    for reviewer in ('R1','R2'):
        grades={}
        for node in sorted({r['node'] for r in key.values()}):
            folder='model_method_reviews' if kind=='main' else 'model_'+kind+'_reviews'
            data=read(P2/folder/reviewer/(node+'.json'))
            if data['reviewer_model']!='gpt-6.1-sol' or data['reasoning_effort']!='medium' or data['human_review_performed'] is not False or data['public_input_sha256']!=g.digest(selected()[node]):raise ValueError('Reviewer provenance/hash')
            expected={k for k,v in key.items() if v['node']==node}
            if {r['anonymous_id'] for r in data['items']}!=expected or len(data['items'])!=len(expected):raise ValueError('Missing or duplicate ratings')
            for r in data['items']:
                dims=(('clarification_quality',4),('history_use',4)) if kind=='branch' else (('U',4),('R',4),('E',2),('P',1))
                for dim,high in dims:
                    if isinstance(r[dim],bool) or not isinstance(r[dim],(int,float)) or not 0<=r[dim]<=high:raise ValueError('Invalid rating')
                if not isinstance(r['S'],bool):raise ValueError('Invalid severe flag')
                if kind=='branch':
                    if not isinstance(r['leading_or_unsupported'],bool) or not isinstance(r['fact_conflict'],bool):raise ValueError('Branch binary flags')
                    if r['client_compliance_risk'] not in ('low','medium','high'):raise ValueError('Compliance risk')
                elif r['P'] not in (0,1):raise ValueError('Invalid presupposition flag')
                grades[r['anonymous_id']]=r
        reviews[reviewer]=grades
    numeric=('clarification_quality','history_use') if kind=='branch' else ('U','R')
    binary=('leading_or_unsupported','fact_conflict','S') if kind=='branch' else ('P','S')
    for id_,meta in key.items():
        a=reviews['R1'][id_];b=reviews['R2'][id_]
        dims=[d for d in binary if a[d]!=b[d]]+[d for d in numeric if abs(a[d]-b[d])>1]
        if dims:
            packet=read(P2/('blind_main' if kind=='main' else 'blind_'+kind)/'R1'/(meta['node']+'.json'))
            field='branches' if kind=='branch' else 'answers'
            item=next(r for r in packet[field] if r['anonymous_id']==id_)
            disputes.append({'anonymous_id':id_,'node':meta['node'],'dimensions':dims,'item':item,'R1':a,'R2':b})
    for node in sorted({r['node'] for r in disputes}):
        write(P2/('adjudication_'+kind)/'node_packets'/(node+'.json'),{'node':node,'public_input':selected()[node],'items':[r for r in disputes if r['node']==node]})
    write(P2/('adjudication_'+kind)/'order.json',{'nodes':sorted({r['node'] for r in disputes}),'items':len(disputes)})
    write(P2/(kind+'_review_validation.json'),{'items':len(key),'independent_ratings':len(key)*2,'disputes':len(disputes),'human_review_performed':False})
    write(P2/(kind+'_paired_reviews.json'),{'key':key,'reviews':reviews,'disputes':disputes})
    print({'kind':kind,'items':len(key),'disputes':len(disputes)})

def finalize(kind):
    data=read(P2/(kind+'_paired_reviews.json'));decisions={}
    required={r['anonymous_id']:r for r in data['disputes']}
    if required:
        for node in sorted({r['node'] for r in required.values()}):
            result=read(P2/('adjudication_'+kind)/'decisions'/(node+'.json'))
            if result['reviewer_model']!='gpt-6.1-sol' or result['reasoning_effort']!='medium' or result['human_review_performed'] is not False or result['public_input_sha256']!=g.digest(selected()[node]):raise ValueError('Adjudicator provenance')
            for row in result['items']:decisions[row['anonymous_id']]=row
        if set(decisions)!=set(required):raise ValueError('Exactly current disputes require arbitration')
    rows=[]
    numeric=('clarification_quality','history_use') if kind=='branch' else ('U','R','E')
    binary=('leading_or_unsupported','fact_conflict','S') if kind=='branch' else ('P','S')
    for id_,meta in data['key'].items():
        a=data['reviews']['R1'][id_];b=data['reviews']['R2'][id_];values={d:(a[d]+b[d])/2 for d in numeric}
        for d in binary:values[d]=a[d] if a[d]==b[d] else decisions[id_][d]
        for d in required.get(id_,{}).get('dimensions',[]):
            if d in numeric:values[d]=decisions[id_][d]
        for d in numeric:
            if not 0<=values[d]<=(2 if d=='E' else 4):raise ValueError('Arbitrated numeric range')
        for d in binary:
            if d=='P':
                if values[d] not in (0,1):raise ValueError('Arbitrated P range')
            elif not isinstance(values[d],bool):raise ValueError('Arbitrated flag')
        rows.append({**meta,'anonymous_id':id_,**values,'R1':a,'R2':b,'adjudication':decisions.get(id_)})
    write(P2/('scored_'+kind+'.json'),{'human_review_performed':False,'rows':rows,'numeric_policy':'mean of two independent raters; differences >1 use third adjudicated score','binary_policy':'agreement, otherwise third model arbitration'})
    groups=collections.defaultdict(list)
    for row in rows:groups[(row['node'],row.get('method',row.get('condition')))].append(row)
    table=[]
    for (node,group),items in sorted(groups.items()):
        row={'node':node,'group':group,'samples':len(items)}
        row.update({'mean_'+d:sum(r[d] for r in items)/len(items) for d in numeric})
        row.update({d+'_count':sum(r[d] for r in items) for d in binary});table.append(row)
    with (P2/('results_'+kind+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(table[0]));writer.writeheader();writer.writerows(table)
    write(P2/('results_'+kind+'.json'),{'table':table,'human_review_performed':False});print({'kind':kind,'final':len(rows)})

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('publish','collect','finalize'));parser.add_argument('kind',choices=('main','probe','branch'));args=parser.parse_args()
    {'publish':publish,'collect':collect,'finalize':finalize}[args.action](args.kind)
