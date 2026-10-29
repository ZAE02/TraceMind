"""Compute paired history effects and explicit stop/continue decisions."""
import csv
import json
from collections import defaultdict
import generator as g
from p2_validation import P2, ROOT, read, write
from p2_methods import selected

def grade_maps(scope):
    original=scope=='original'
    key=read(P2/'internal'/('ablation_blind_key.json' if original else 'reselected_blind_key.json'))
    base=P2/'model_ablation_reviews' if original else P2/'reselection/model_answer_reviews'
    risk=read(P2/'review_order_risk.json')['affected_nodes'] if original else {}
    ratings={}
    for reviewer in ('R1','R2'):
        rows={}
        for node in sorted({meta['node'] for meta in key.values()}):
            path=base/reviewer/(node+'.json')
            if node in risk.get(reviewer,[]):path=P2/'model_ablation_review_corrections'/reviewer/(node+'.json')
            data=read(path)
            rows.update({item['anonymous_id']:item for item in data['items']})
        if set(rows)!=set(key):raise ValueError('Rating keys not complete')
        ratings[reviewer]=rows
    path=P2/'adjudication/decisions_orderfixed.json' if original else P2/'adjudication_reselected/decisions.json'
    adjud=read(path)
    if (adjud.get('reviewer_model')!='gpt-6.1-sol' or adjud.get('reasoning_effort')!='medium'
            or adjud.get('human_review_performed') is not False):raise ValueError('Adjudicator identity')
    decisions={item['anonymous_id']:item for item in adjud['items']}
    required={id_ for id_ in key if ratings['R1'][id_]['P']!=ratings['R2'][id_]['P']}
    if set(decisions)!=required:raise ValueError('Exactly all current P disagreements require decisions')
    rows=[]
    for id_,meta in key.items():
        a,b=ratings['R1'][id_],ratings['R2'][id_]
        P=a['P'] if a['P']==b['P'] else decisions[id_]['P']
        rows.append({**meta,'anonymous_id':id_,'scope':scope,
            'U':(a['U']+b['U'])/2,'R':(a['R']+b['R'])/2,'E':(a['E']+b['E'])/2,'P':P,
            'S':a['S'] or b['S'],'rater_1':a,'rater_2':b,'P_adjudication':decisions.get(id_)})
    return rows

def main():
    rows=grade_maps('original')+grade_maps('reselected')
    write(P2/'scored_history_answers.json',{'human_review_performed':False,'answers':rows,
        'model_judges':'two independent GPT-6.1 Sol/medium plus third for P disagreement',
        'original_review_order_corrections':'review_order_risk.json'})
    grouped=defaultdict(list)
    for row in rows:grouped[(row['scope'],row['node'],row['condition'])].append(row)
    table=[]
    for (scope,node,condition),group in sorted(grouped.items()):
        table.append({'scope':scope,'node':node,'condition':condition,'samples':len(group),
            'mean_U':sum(r['U'] for r in group)/len(group),'mean_R':sum(r['R'] for r in group)/len(group),
            'mean_E':sum(r['E'] for r in group)/len(group),'P_count':sum(r['P'] for r in group),
            'P_rate':sum(r['P'] for r in group)/len(group),'S_count':sum(r['S'] for r in group)})
    with (P2/'results_by_checkpoint.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(table[0]));writer.writeheader();writer.writerows(table)
    lookup={(r['scope'],r['node'],r['condition']):r for r in table}
    effects=[]
    for node,public in selected().items():
        confirms=[read(P2/'reselection/model_confirmations'/r/(node+'.json')) for r in ('R1','R2')]
        h1=lookup[('reselected',node,'H1_session')];h2=lookup[('reselected',node,'H2_full')]
        sufficient=(h1['mean_U']>=3.5 and h1['mean_R']>=3.5 and abs(h2['mean_U']-h1['mean_U'])<0.5
                    and abs(h2['mean_R']-h1['mean_R'])<0.5 and h1['P_count']==h2['P_count'])
        effects.append({'node':node,'case_id':public['case_id'],'both_confirmed':all(c['needs_history'] for c in confirms),
            'H1':h1,'H2':h2,'delta_U':h2['mean_U']-h1['mean_U'],'delta_R':h2['mean_R']-h1['mean_R'],
            'current_session_sufficient_and_equivalent':sufficient})
    confirmed=[r for r in effects if r['both_confirmed']]
    cases={r['case_id'] for r in confirmed}
    most_sufficient=sum(r['current_session_sufficient_and_equivalent'] for r in effects)>len(effects)/2
    passed=len(confirmed)>=3 and len(cases)>=2 and not most_sufficient
    gate={'kind':'exploratory_model_assisted_empirical_gate','passed':passed,'selected_nodes':list(selected()),
        'confirmed_nodes':len(confirmed),'confirmed_cases':len(cases),'most_current_session_sufficient':most_sufficient,
        'effects':effects,'protocol':'empirical_gate_protocol.json','human_review_performed':False,'checked_at':g.now()}
    write(P2/'empirical_gate.json',gate)
    print(json.dumps({'passed':passed,'confirmed_nodes':len(confirmed),'cases':len(cases),
        'original_P':sum(r['P'] for r in rows if r['scope']=='original'),
        'reselected_P':sum(r['P'] for r in rows if r['scope']=='reselected'),
        'effects':[(r['node'],r['delta_U'],r['delta_R']) for r in effects]}))

if __name__=='__main__':main()
