"""Check actual P2 requests, frozen prefixes, memory chain and branch provenance."""
import collections
import json
import re
import generator as g
from p2_validation import ROOT,P2,read,write,expected_public,PROMPT
import p2_methods as m

def main():
    errors=[];checks=collections.Counter();logs=[]
    for path in (ROOT/'generation_logs').glob('p2_*calls.jsonl'):
        for line in path.read_text(encoding='utf-8').splitlines():
            row=json.loads(line);row['log_file']=path.name;logs.append(row)
    calls={r['logical_call_id']:r for r in logs if r.get('status')=='success' and 'logical_call_id' in r}
    # generator uses completed/success status depending on source version.
    calls.update({r['logical_call_id']:r for r in logs if r.get('spoken_text') and r.get('response',{}).get('status')=='completed' and 'logical_call_id' in r})
    for r in logs:
        payload=r['request']
        if 'input' not in payload or 'model' not in payload:continue
        if isinstance(payload.get('input'),str):continue # embedding requests
        if 'store' not in payload:continue # embedding input is a list
        if payload.get('store') is not False or payload.get('stream') is not False or 'previous_response_id' in payload:errors.append('Server state/stream '+r['logical_call_id'])
        context=json.loads(payload['input'][-1]['content'])
        if r.get('purpose')!='p2_client_simulation':
            forbidden={'frozen_private_experience','client_profile','private_life_history','private_experiences','researcher_annotations','gold','trajectory_label','source_current_card'}
            def walk(value):
                if isinstance(value,dict):
                    for k,v in value.items():
                        if k in forbidden:errors.append('Forbidden counselor key '+k+' '+r['logical_call_id'])
                        walk(v)
                elif isinstance(value,list):
                    for v in value:walk(v)
            walk(context)
        if r.get('purpose') in ('method_comparison','reselected_ablation','dated_memory_probe','branch_counselor'):
            if payload['input'][0]['content']!=PROMPT or payload['temperature']!=.7 or payload['reasoning']['effort']!='low' or payload['max_output_tokens']!=4096:errors.append('Answer task mismatch '+r['logical_call_id'])
            node=r.get('node')
            if node in m.selected():
                public=m.selected()[node]
                historical_ids={x for x in m.all_ids(context) if x.startswith('C0')}
                if not historical_ids<=m.all_ids(public):errors.append('Future/foreign evidence in actual request '+r['logical_call_id'])
                if r.get('purpose')!='branch_counselor':
                    if context['current_user_utterance']!=public['current_user_utterance']:errors.append('Changed latest utterance')
                    # H0 intentionally has no prefix; other methods share the full prefix.
                    if r.get('condition')!='H0_utterance' and context['current_session_dialogue']!=public['current_session_dialogue']:errors.append('Changed current session prefix')
        checks['actual_response_requests']+=1
    for node,public in m.selected().items():
        previous,prefix,latest=expected_public(public['case_id'],public['current_session'],public['current_user_utterance']['id'])
        if previous!=public['previous_session_dialogues'] or prefix!=public['current_session_dialogue'] or latest!=public['current_user_utterance']:errors.append('Frozen actual prefix '+node)
        checks['selected_public_prefixes']+=1
        for sample in (1,2):
            a=read(P2/'method_answers'/('%s_B1_full_%d.json'%(node,sample)))
            b=read(P2/'reselection/answers'/('%s_H2_full_%d.json'%(node,sample)))
            if a['call_id']!=b['call_id'] or a['request_sha256']!=b['request_sha256']:errors.append('Full context reuse '+node)
    for method in ('B3_generic_rollup','B4_psych_rollup'):
        for node,public in m.selected().items():
            previous={'entries':[]}
            for session in public['previous_session_dialogues']:
                n=session['session_number'];snapshot=read(P2/'memory_snapshots'/method/public['case_id']/('S%02d.json'%n))
                if snapshot['input_previous_memory_sha256']!=g.digest(previous) or snapshot['new_session_sha256']!=g.digest(session):errors.append('Memory chain '+method+node+str(n))
                if snapshot['tokenizer_tokens']>3000 or m.tokens(snapshot['state'])>3000:errors.append('Budget '+method+node+str(n))
                if not m.all_ids(snapshot['state'])<=m.all_ids({'previous':previous,'session':session}):errors.append('Memory invisible evidence')
                previous=snapshot['state'];checks['chronological_bounded_snapshots']+=1
    branches=[]
    for path in sorted((P2/'interactive_branches').glob('*/dialogue.json')):
        row=read(path);branches.append(row);public=m.selected()[row['node']]
        state=read(P2/'simulator_states'/(row['node']+'.json'))
        if row['private_state_sha256']!=state['state_sha256']:errors.append('Changed simulator start')
        if [r['role'] for r in row['utterances']]!=['counselor','client','counselor','client']:errors.append('Branch sequence')
        for utterance in row['utterances']:
            if utterance['source_call_id'] not in calls:errors.append('Missing actual call '+utterance['source_call_id'])
        for i in (1,2):
            record=read(path.parent/('client_%d.json'%i));payload=calls[record['call_id']]['request'];context=json.loads(payload['input'][-1]['content'])
            if g.digest(context['frozen_private_experience'])!=state['state_sha256']:errors.append('Unequal client state')
            if context['previous_session_dialogues']!=public['previous_session_dialogues']:errors.append('Client future past history')
            expected=public['current_session_dialogue']+[public['current_user_utterance']]+[{k:r[k] for k in ('id','role','text')} for r in row['utterances'][:1 if i==1 else 3]]
            if context['current_session_dialogue']!=expected:errors.append('Branch actual prefix mismatch')
        counselor=read(path.parent/'counselor_2.json');ccontext=json.loads(calls[counselor['call_id']]['request']['input'][-1]['content'])
        if ccontext['current_user_utterance']!={k:row['utterances'][1][k] for k in ('id','role','text')}:errors.append('Wrong branch counselor latest')
        if ccontext['current_session_dialogue']!=public['current_session_dialogue']+[public['current_user_utterance']]+[{k:row['utterances'][0][k] for k in ('id','role','text')}]:errors.append('Counselor saw wrong branch prefix')
        checks['same_start_actual_branches']+=1
    # Scan only project-authored text, excluding dependencies/tokenizer; do not print any matching value.
    secret_pattern=re.compile(r'sk-ws-[A-Za-z0-9_.-]{20,}')
    for path in ROOT.rglob('*'):
        if not path.is_file() or path.suffix not in ('.py','.md','.json','.jsonl','.txt','.csv'):continue
        if any(x in path.parts for x in ('dependencies','tokenizer','.git','__pycache__')):continue
        if secret_pattern.search(path.read_text(encoding='utf-8-sig',errors='replace')):errors.append('Credential-like content in '+str(path.relative_to(ROOT)))
    cost=collections.defaultdict(lambda:collections.Counter())
    for r in logs:
        purpose=r.get('purpose','embedding');cost[purpose]['attempts']+=1
        usage=r.get('response',{}).get('usage',{})
        if not usage:cost[purpose]['attempts_without_reported_usage']+=1
        for dim in ('input_tokens','output_tokens','total_tokens'):
            fallback=usage.get('prompt_tokens',0) if dim=='input_tokens' else usage.get('completion_tokens',0) if dim=='output_tokens' else 0
            cost[purpose][dim]+=usage.get(dim,fallback)
        if r.get('response',{}).get('status')=='incomplete':cost[purpose]['incomplete_responses']+=1
        if r.get('error'):cost[purpose]['error_attempts']+=1
    write(P2/'resource_usage.json',{'api_attempt_usage_including_failures':dict(cost),'usage_limit':'Logged usage only. Missing-usage/time-out attempts may incur additional unknown service work; these sums are not a billing reconciliation.','reviewer_usage':'Subagent token usage not exposed in these API logs; excluded from API token sums.','currency_cost':'Not inferred without verified price/rate and bill.'})
    output={'status':'passed' if not errors else 'failed','checks':dict(checks),'errors':errors,'human_review_performed':False,'checked_at':g.now()}
    write(P2/'final_integrity_audit.json',output);print(json.dumps(output,ensure_ascii=True))
    if errors:raise ValueError('Final integrity errors')

if __name__=='__main__':main()
