"""Dated-memory probes and independent, same-start client simulation branches."""
import argparse
import copy
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
import generator as g
from p2_validation import ROOT,P2,read,write,PROMPT,condition_input
import p2_methods as m

NODES=('N04','N06')
METHODS=('B2_temporal_RAG','B3_generic_rollup','B4_psych_rollup')

def actual_reply(row):
    parsed=row.get('parsed_answer')
    if isinstance(parsed,dict) and isinstance(parsed.get('next_counselor_utterance'),str):
        return parsed['next_counselor_utterance'],'strict_json_field'
    # No rewording: decode just the original, independently valid JSON string field.
    match=re.search(r'"next_counselor_utterance"\s*:\s*("(?:\\.|[^"\\])*")',row['raw_text'])
    if not match:raise ValueError('Cannot extract an actual counselor utterance from raw answer')
    return json.loads(match.group(1)),'exact_standalone_json_string_field; whole answer format invalid'

def probe(secret,allow_partial=False):
    pending=[];tasks=[]
    for node in NODES:
        public=m.selected()[node];cid=public['case_id'];number=public['current_session']
        latest_path=P2/'memory_snapshots/B4_psych_rollup'/cid/('S%02d.json'%(number-1))
        if not latest_path.exists():
            if allow_partial:pending.append(node);continue
            raise ValueError('Psychological memory still incomplete')
        old=read(P2/'memory_snapshots/B4_psych_rollup'/cid/'S01.json')['state']
        prior=read(latest_path)['state']
        context={'previous_memory':prior,'new_session':{'session_number':number,'complete_session':False,
            'utterances':public['current_session_dialogue']+[public['current_user_utterance']]}}
        memo_row=m.api_record(secret,m.PSYCH_MEMORY,context,P2/'probe_memos'/(node+'_updated_raw.json'),
            'probe_memo_update',max_output_tokens_override=8192,request_timeout_seconds_override=300,node=node,
            note='partial-current-session probe only; does not overwrite B4 main prototype state')
        updated,repair=m.structural_memory_parse(memo_row);updated,dropped=m.fit_memory(updated)
        if not m.all_ids(updated)<=m.all_ids(context):raise ValueError('Probe memo cites unavailable IDs')
        memos={'M_old':{'formed_after_session':1,'verification':'tentative; formed from evidence available then, may need rechecking','state':old},
               'M_updated':{'formed_after_session':number,'verification':'tentative; formed from evidence available then, may need rechecking','state':updated}}
        write(P2/'probe_memos'/(node+'.json'),{'node':node,'memos':memos,'current_updated_call_id':memo_row['call_id'],
            'syntax_repair':repair,'dropped_entries':dropped,'old_tokens':m.tokens(old),'updated_tokens':m.tokens(updated),
            'notice':'Dated, tentative hypotheses; no hidden trajectories or researcher Gold. Different memo lengths are reported.'})
        for condition in ('M0','M_old','M_updated'):
            for sample in (1,2):tasks.append((node,public,condition,sample,memos))
    def one(t):
        node,public,condition,sample,memos=t;path=P2/'probe_answers'/('%s_%s_%d.json'%(node,condition,sample))
        if condition=='M0':
            source=P2/'reselection/answers'/('%s_H2_full_%d.json'%(node,sample))
            row=copy.deepcopy(read(source));row.update(condition=condition,reused_from=str(source.relative_to(ROOT)))
            write(path,row);return row
        context=condition_input(public,'H2_full');context['dated_psychological_memo']=memos[condition]
        return m.api_record(secret,PROMPT,context,path,'dated_memory_probe',node=node,condition=condition,sample=sample,
                            public_input_sha256=g.digest(public))
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(one,tasks))
    write(P2/'probe_run_status.json',{'status':'partial' if pending else 'completed','answers':len(rows),
        'nodes':list(NODES),'pending':pending,'M0_reused':sum(bool(r.get('reused_from')) for r in rows),
        'scope':'mechanism probe only; excluded from method ranking','updated_at':g.now()})

def frozen_client(node,public):
    plan=read(ROOT/'configs/case_plans'/(public['case_id']+'.json'))
    number=public['current_session'];card=next(c for c in plan['session_cards'] if c['session_number']==number)
    current=copy.deepcopy({key:card[key] for key in ('life_events','private_experiences','still_unspoken_details')})
    excluded=[]
    if node=='N04':
        excluded.append({'source':current['life_events'][1],
            'reason':'Future completion of remaining work and the other person\'s reaction are not established at this cut.'})
        current['life_events']=current['life_events'][:1]
        excluded.append({'source':current['still_unspoken_details'][0],
            'reason':'Disclosure scheduling instruction, not a private fact; some reasons already disclosed in the public prefix.'})
        current['still_unspoken_details']=current['still_unspoken_details'][1:]
        current['known_unknowns']=['阿敏明天是否做完其他部分尚未知。','不预设阿敏实际认为陈宁小气。']
    occurred=[{'session_number':c['session_number'],'life_events':c['life_events']}
              for c in plan['session_cards'] if c['session_number']<number]
    if node=='N04':
        excluded.append({'source':'Earlier planned life-event cards',
            'reason':'Some dates/completion statements differ from actual generated dialogue (e.g., S4 Tuesday-to-Thursday). Use actual earlier client reports, not planned outcomes.'})
        occurred=[{'session_number':s['session_number'],
                   'life_events':[next(r['text'] for r in s['utterances'] if r['role']=='client')]}
                  for s in public['previous_session_dialogues']]
    state={'client_profile':plan['client_profile'],'private_life_history':plan['private_life_history'],
        'occurred_life_events':occurred,
        'current_private_experience':current}
    destination=P2/'simulator_states'/(node+'.json')
    if destination.exists() and read(destination)['state_sha256']!=g.digest(state):
        raise ValueError('Frozen private simulator state changed; create an explicit new branch version')
    write(destination,{'node':node,'state':state,'state_sha256':g.digest(state),
        'source_current_card':card,'excluded_items':excluded,'public_input_sha256':g.digest(public),
        'notice':'Synthetic private simulation state, shared across methods/replicates, not evaluation Gold. No later session or original post-freeze client responses supplied.'})
    return state

def simulate_client(secret,context,path,**meta):
    active=m.config();active['call_log_filename']='p2_client_simulation_calls.jsonl'
    prompt=(ROOT/'configs/client_system_prompt.txt').read_text(encoding='utf-8')
    prompt+='\n这是同一场连续谈话。不要离开咨询去完成工作，不把尚未发生的行动写成已完成。只依据已知事实回答，允许记不清、保留疑惑或不同意咨询师。不要新增重大生活事实，不推测别人的真实心理。'
    payload=g.build_payload('client',prompt,active,context);fingerprint=g.digest(payload)
    if path.exists():
        saved=read(path)
        if saved['request_sha256']!=fingerprint:raise ValueError('Changed client branch request')
        return saved
    text,call_id,response=g.call_api(ROOT,active,payload,dict(purpose='p2_client_simulation',**meta),secret)
    row={'status':'completed',**meta,'request_sha256':fingerprint,'call_id':call_id,'model_returned':response.get('model'),
        'raw_text':text,'usage':response.get('usage'),'created_at':g.now(),
        'call_log_reference':'generation_logs/p2_client_simulation_calls.jsonl'}
    write(path,row);return row

def one_branch(secret,node,public,method,replicate,state):
    id_='BR_'+g.digest({'node':node,'method':method,'replicate':replicate})[:12]
    directory=P2/'interactive_branches'/id_
    first=read(P2/'method_answers'/('%s_%s_1.json'%(node,method)))
    reply,extraction=actual_reply(first)
    rows=[{'id':id_+'_U01','role':'counselor','text':reply,'source_call_id':first['call_id'],'extraction':extraction}]
    for round_ in (1,2):
        context={'session_background':public['session_background'],
            'previous_session_dialogues':public['previous_session_dialogues'],
            'current_session_dialogue':public['current_session_dialogue']+[public['current_user_utterance']]+
                                       [{'id':r['id'],'role':r['role'],'text':r['text']} for r in rows],
            'frozen_private_experience':state}
        client=simulate_client(secret,context,directory/('client_%d.json'%round_),branch_id=id_,round=round_,
                               node=node,method=method,replicate=replicate,private_state_sha256=g.digest(state))
        rows.append({'id':id_+'_U%02d'%(len(rows)+1),'role':'client','text':client['raw_text'],'source_call_id':client['call_id']})
        if round_==1:
            dynamic=copy.deepcopy(public)
            dynamic['current_session_dialogue']=(public['current_session_dialogue']+[public['current_user_utterance']]+
                [{'id':r['id'],'role':r['role'],'text':r['text']} for r in rows[:-1]])
            dynamic['current_user_utterance']={'id':rows[-1]['id'],'role':'client','text':rows[-1]['text']}
            counselor_context,info=m.method_context(secret,dynamic,method,id_+'_turn2')
            row=m.api_record(secret,PROMPT,counselor_context,directory/'counselor_2.json','branch_counselor',
                branch_id=id_,node=node,method=method,replicate=replicate,round=2,memory_info=info)
            text,extraction=actual_reply(row)
            rows.append({'id':id_+'_U%02d'%(len(rows)+1),'role':'counselor','text':text,'source_call_id':row['call_id'],'extraction':extraction})
    record={'branch_id':id_,'node':node,'method':method,'replicate':replicate,'rounds':2,
        'status':'completed','public_input_sha256':g.digest(public),'private_state_sha256':g.digest(state),
        'initial_answer_sample':1,'utterances':rows,'client_model':m.config()['model'],'counselor_model':m.config()['model'],
        'notice':'Initial counselor answer reused. Two new client responses and one new counselor response; no original post-freeze client reply spliced in.'}
    write(directory/'dialogue.json',record)
    text=['# '+id_, '', '> 拟真分支；匿名初始方法；同一冻结私有状态；2组咨询师→来访者往返。','']
    for r in rows:text+=['**'+('咨询师' if r['role']=='counselor' else '来访者')+'**：'+r['text'],'']
    (directory/'dialogue.md').write_text('\n'.join(text),encoding='utf-8')
    print(id_+' branch completed',flush=True)
    return record

def branches(secret,allow_partial=False):
    tasks=[];pending=[]
    for node in NODES:
        public=m.selected()[node]
        if any(not (P2/'method_answers'/('%s_%s_1.json'%(node,method))).exists() for method in METHODS):
            if allow_partial:pending.append(node);continue
            raise ValueError('Method answers not yet complete')
        state=frozen_client(node,public)
        for method in METHODS:
            for replicate in (1,2):tasks.append((node,public,method,replicate,state))
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(lambda t:one_branch(secret,*t),tasks))
    write(P2/'branch_run_status.json',{'status':'partial' if pending else 'completed','branches':len(rows),'pending':pending,
        'nodes':list(NODES),'rounds_per_branch':2,'replicates_per_method':2,
        'initial_answer_selection':'sample1 for all groups, fixed before branch outcomes; no cherry-picking',
        'human_review_performed':False,'updated_at':g.now()})

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=('probes','probes_available','branches','branches_available'))
    args=parser.parse_args();secret=os.environ.get('DASHSCOPE_API_KEY') or sys.stdin.readline().strip()
    if not secret:raise ValueError('API key required on stdin')
    os.environ['DASHSCOPE_API_KEY']=secret
    if not read(P2/'empirical_gate.json')['passed']:raise ValueError('Empirical gate required')
    if args.phase.startswith('probes'):probe(secret,args.phase.endswith('available'))
    else:branches(secret,args.phase.endswith('available'))

if __name__=='__main__':main()
