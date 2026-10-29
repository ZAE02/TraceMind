"""P2 public-only experiments and chronological memory prototypes."""
import argparse
import collections
import copy
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib import request, error

import generator as g
from p2_validation import ROOT, P2, PROMPT, read, write, condition_input

sys.path.insert(0,str(P2/'dependencies'))
from tokenizers import Tokenizer
from json_repair import repair_json
TOKENIZER=Tokenizer.from_file(str(P2/'tokenizer/tokenizer.json'))
BUDGET=3000

def tokens(value):
    text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
    return len(TOKENIZER.encode(text,add_special_tokens=False).ids)

def config():
    return dict(read(ROOT/'configs/generation_config.json'),reasoning_effort='low',role_reasoning_effort={},
                prompt_version='p2-controlled-task-v1',call_log_filename='p2_calls.jsonl')

def selected():
    gate=read(P2/'reselection/node_gate.json')
    if not gate['passed']:raise ValueError('Candidate gate not passed')
    return {id_:read(P2/'reselection/public'/(id_+'.json')) for id_ in gate['selected_nodes']}

def parse(text):
    cleaned=text.strip()
    if cleaned.startswith('```json'):cleaned=cleaned[7:].rsplit('```',1)[0]
    elif cleaned.startswith('```'):cleaned=cleaned[3:].rsplit('```',1)[0]
    try:return json.loads(cleaned)
    except ValueError:return None

def all_ids(context):
    # ID links in bounded summaries are allowed; private facts are never in this context.
    return set(re.findall(r'C0[123]-S0[1-8]-U\d{2}|BR_[A-Za-z0-9_]+',json.dumps(context,ensure_ascii=False)))

def api_record(secret,prompt,context,path,purpose,max_output_tokens_override=None,request_timeout_seconds_override=None,**meta):
    active=config();active['call_log_filename']='p2_'+purpose+'_calls.jsonl'
    if max_output_tokens_override is not None:active['max_output_tokens']=max_output_tokens_override
    if request_timeout_seconds_override is not None:active['request_timeout_seconds']=request_timeout_seconds_override
    payload=g.build_payload('counselor',prompt,active,context)
    fingerprint=g.digest(payload)
    if path.exists():
        saved=read(path)
        if saved['request_sha256']!=fingerprint:raise ValueError('Changed request for existing record: '+str(path))
        return saved
    text,call_id,response=g.call_api(ROOT,active,payload,dict(purpose=purpose,request_sha256=fingerprint,**meta),secret)
    parsed=parse(text)
    record={'status':'completed','purpose':purpose,**meta,'request_sha256':fingerprint,
        'context_sha256':g.digest(context),'call_id':call_id,'model_returned':response.get('model'),
        'parameters':{'temperature':active['temperature'],'reasoning_effort':'low','max_output_tokens':active['max_output_tokens']},
        'transport_timeout_seconds':active['request_timeout_seconds'],
        'created_at':g.now(),'raw_text':text,'parsed_answer':parsed,'usage':response.get('usage'),
        'call_log_reference':'generation_logs/'+active['call_log_filename'],
        'visible_evidence_ids':sorted(all_ids(context)),'annotation_visible':False,'private_state_visible':False}
    write(path,record)
    print(purpose+' '+path.stem+' saved',flush=True)
    return record

def run_reselected_ablation(secret):
    tasks=[]
    for node,public in selected().items():
        for condition in ('H0_utterance','H1_session','H2_full'):
            for sample in (1,2):tasks.append((node,public,condition,sample))
    def one(t):
        node,public,condition,sample=t
        context=condition_input(public,condition)
        return api_record(secret,PROMPT,context,P2/'reselection/answers'/('%s_%s_%d.json'%(node,condition,sample)),
                          'reselected_ablation',node=node,condition=condition,sample=sample,
                          public_input_sha256=g.digest(public))
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(one,tasks))
    with (P2/'reselection/history_ablation.jsonl').open('w',encoding='utf-8') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    write(P2/'reselection/ablation_status.json',{'status':'completed','answers':len(rows),'updated_at':g.now()})

COMMON_MEMORY='只用上一份记忆状态与这次新增会话更新。输出JSON，顶层entries数组；每项有text、type、speaker、evidence_ids。保留重要人物、具体事件、感受、关系与尚未确认事项，按重要性排序。区分咨询师猜测与来访者确认，不把计划当成完成，不作诊断；关键内容保留真实发言ID和说话者。保持精简，约2000–2500 tokens。'
GENERIC_MEMORY=COMMON_MEMORY+'这是普通人物和事件的滚动摘要。可保留情绪体验和变化，但无需构造专门心理解释理论。'
PSYCH_MEMORY=COMMON_MEMORY+'这是心理理解的持续维护原型。每项额外注明形成/更新Session、interpretation/status/scope；区分当前有支持的解释、仅有可能的解释、受新证据限制或挑战的旧解释。记录支持与冲突evidence_ids以及未知事项。不要把旧假设一直固化，也不要每次都强行宣称变化。'

def fit_memory(state):
    if not isinstance(state,dict) or not isinstance(state.get('entries'),list):raise ValueError('Invalid memory JSON')
    bounded=copy.deepcopy(state);dropped=[]
    while tokens(bounded)>BUDGET:
        if not bounded['entries']:raise ValueError('Non-entry memory exceeds budget')
        dropped.append(bounded['entries'].pop())
    return bounded,dropped

def structural_memory_parse(row):
    raw=row['parsed_answer']
    if raw is not None:return raw,None
    text=row['raw_text'].strip()
    if text.startswith('```json'):text=text[7:].rsplit('```',1)[0]
    elif text.startswith('```'):text=text[3:].rsplit('```',1)[0]
    repaired,actions=repair_json(text,return_objects=True,logging=True,ensure_ascii=False)
    if not isinstance(repaired,dict) or not isinstance(repaired.get('entries'),list):
        raise ValueError('Structural repair did not yield entries')
    count=len(re.findall(r'(?m)^\s*"text"\s*:',text))
    if len(repaired['entries'])!=count or all_ids(repaired)!=all_ids(text):
        raise ValueError('Structural repair changed entry count or evidence IDs')
    return repaired,{'method':'json-repair 0.44.1, syntax only; raw API output retained',
                     'raw_text_sha256':g.digest(row['raw_text']),'parsed_sha256':g.digest(repaired),
                     'actions':actions}

def build_memory_case(secret,cid,previous):
    for method,prompt in (('B3_generic_rollup',GENERIC_MEMORY),('B4_psych_rollup',PSYCH_MEMORY)):
        state={'entries':[]}
        for session in previous:
            number=session['session_number']
            path=P2/'memory_snapshots'/method/cid/('S%02d.json'%number)
            context={'previous_memory':state,'new_session':session}
            raw_path=path.with_name(path.stem+'_raw.json')
            recovery=None
            if raw_path.exists():
                output_limit=read(raw_path)['parameters']['max_output_tokens']
            else:
                output_limit=4096
                attempted=g.digest(g.build_payload('counselor',prompt,config(),context))
                log=ROOT/'generation_logs/p2_incremental_memory_update_calls.jsonl'
                if log.exists():
                    failed=[json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()
                            if json.loads(line).get('request_sha256')==attempted
                            and json.loads(line).get('response',{}).get('status')=='incomplete']
                    if failed:output_limit=8192;recovery=failed[-1]['logical_call_id']
                    larger=[json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()
                            if json.loads(line).get('case_id')==cid and json.loads(line).get('session_number')==number
                            and json.loads(line).get('method')==method and json.loads(line).get('request',{}).get('max_output_tokens')==8192
                            and json.loads(line).get('response',{}).get('status')=='incomplete']
                    if larger:output_limit=16384;recovery=larger[-1]['logical_call_id']
            try:
                row=api_record(secret,prompt,context,raw_path,'incremental_memory_update',
                    max_output_tokens_override=output_limit,request_timeout_seconds_override=300,
                    case_id=cid,session_number=number,method=method)
            except g.GenerationError as exc:
                if 'not completed: incomplete' not in str(exc) or output_limit!=4096:raise
                log=ROOT/'generation_logs/p2_incremental_memory_update_calls.jsonl'
                failed=[json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()
                        if json.loads(line).get('case_id')==cid and json.loads(line).get('session_number')==number
                        and json.loads(line).get('method')==method and json.loads(line).get('response',{}).get('status')=='incomplete']
                recovery=failed[-1]['logical_call_id'] if failed else None
                row=api_record(secret,prompt,context,raw_path,'incremental_memory_update',
                    max_output_tokens_override=8192,request_timeout_seconds_override=300,
                    case_id=cid,session_number=number,method=method,
                    incomplete_recovery_from=recovery)
            raw,repair=structural_memory_parse(row);bounded,dropped=fit_memory(raw)
            if not all_ids(bounded)<=all_ids(context):raise ValueError('Memory invents unavailable evidence IDs')
            if path.exists():
                old=read(path)
                if old['state']!=bounded:raise ValueError('Memory snapshot changed')
            write(path,{'method':method,'case_id':cid,'last_completed_session':number,'state':bounded,
                'state_sha256':g.digest(bounded),'tokenizer_tokens':tokens(bounded),'budget':BUDGET,
                'tokenizer_reference':'Qwen/Qwen3.8-27B public tokenizer; hosted tokenizer identity unverified',
                'input_previous_memory_sha256':g.digest(state),'new_session_sha256':g.digest(session),
                'source_call_id':row['call_id'],'dropped_entries':dropped,'raw_state_tokens':tokens(raw),
                'structural_repair':repair,
                'incomplete_recovery_from':recovery or row.get('incomplete_recovery_from'),
                'update_scope':'previous bounded memory + new session only; no complete-history backfill',
                'annotation_visible':False,'private_state_visible':False})
            state=bounded

def run_memory(secret):
    tasks={}
    for public in selected().values():
        cid=public['case_id'];previous=public['previous_session_dialogues']
        if cid not in tasks or len(previous)>len(tasks[cid]):tasks[cid]=previous
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda t:build_memory_case(secret,*t),tasks.items()))
    print('Chronological generic and psychological memory snapshots completed.',flush=True)

def embedding(secret,texts):
    payload={'model':'qwen3.7-text-embedding','input':texts,'dimensions':1024,'encoding_format':'float'}
    fingerprint=g.digest(payload);path=P2/'retrieval/embedding_cache'/(fingerprint+'.json')
    if path.exists():return read(path)['vectors']
    active=config();url=active['base_url'].rstrip('/')+'/embeddings'
    for attempt in range(3):
        try:
            req=request.Request(url,data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
                headers={'Authorization':'Bearer '+secret,'Content-Type':'application/json'})
            with request.urlopen(req,timeout=120) as response:body=json.loads(response.read().decode('utf-8'))
            vectors=[r['embedding'] for r in sorted(body['data'],key=lambda r:r['index'])]
            if len(vectors)!=len(texts):raise ValueError('Embedding count mismatch')
            g.append_log(ROOT,{'purpose':'p2_semantic_retrieval','request':payload,'response':body,
                'status':'success','timestamp':g.now(),'retry_count':attempt},filename='p2_embedding_calls.jsonl')
            write(path,{'model_requested':payload['model'],'model_returned':body.get('model'),
                'request_sha256':fingerprint,'vectors':vectors,'usage':body.get('usage')})
            return vectors
        except error.HTTPError as exc:
            detail=g.scrub(exc.read().decode('utf-8',errors='replace'),secret)
            g.append_log(ROOT,{'purpose':'p2_semantic_retrieval','request':payload,'status':'failed',
                'http_status':exc.code,'error':detail,'timestamp':g.now(),'retry_count':attempt},filename='p2_embedding_calls.jsonl')
            if exc.code not in (429,500,502,503,504) or attempt==2:raise g.GenerationError(detail)
        except (error.URLError,TimeoutError,OSError) as exc:
            if attempt==2:raise g.GenerationError(g.scrub(str(exc),secret))
        time.sleep(2**(attempt+1))

def terms(text):
    chars=re.findall(r'[\u4e00-\u9fff]',text)
    return re.findall(r'[A-Za-z0-9_]+',text.lower())+[''.join(chars[i:i+2]) for i in range(len(chars)-1)]

def cosine(a,b):
    return sum(x*y for x,y in zip(a,b))/(math.sqrt(sum(x*x for x in a))*math.sqrt(sum(y*y for y in b)) or 1)

def retrieve(secret,public,query_rows=None):
    previous=public['previous_session_dialogues'];documents=[]
    for session in previous:
        rows=session['utterances']
        for offset in range(0,len(rows),2):
            documents.append({'session_number':session['session_number'],'utterances':rows[offset:offset+2]})
    query=json.dumps(query_rows if query_rows is not None else public['current_session_dialogue']+[public['current_user_utterance']],ensure_ascii=False)
    texts=[json.dumps(doc,ensure_ascii=False) for doc in documents]
    vectors=[]
    for start in range(0,len(texts),20):vectors+=embedding(secret,texts[start:start+20])
    qvector=embedding(secret,[query])[0]
    frequencies=[collections.Counter(terms(text)) for text in texts]
    queryterms=set(terms(query));avg=sum(sum(f.values()) for f in frequencies)/len(frequencies)
    bm25=[]
    for freq in frequencies:
        score=0;length=sum(freq.values())
        for term in queryterms:
            df=sum(term in f for f in frequencies);idf=math.log(1+(len(frequencies)-df+0.5)/(df+0.5))
            tf=freq[term];score+=idf*tf*2.2/(tf+1.2*(0.25+0.75*length/avg)) if tf else 0
        bm25.append(score)
    maxbm=max(bm25) or 1
    ranked=[]
    for i,doc in enumerate(documents):
        dense=cosine(qvector,vectors[i]);lex=bm25[i]/maxbm
        recency=doc['session_number']/max(1,public['current_session']-1)
        ranked.append({'index':i,'score':0.65*dense+0.25*lex+0.10*recency,'dense':dense,'bm25':lex,'recency':recency})
    ranked.sort(key=lambda x:x['score'],reverse=True)
    # Preserve the best available evidence from the first and latest historical sessions,
    # then relevance-ranked pairs. Never remove counselor hypotheses from the corpus.
    priority=[]
    for session_number in sorted({1,public['current_session']-1}):
        options=[row for row in ranked if documents[row['index']]['session_number']==session_number]
        if options:priority.append(options[0])
    priority+=[row for row in ranked if row not in priority]
    chosen=[]
    for row in priority:
        trial=chosen+[documents[row['index']]]
        if tokens(trial)<=BUDGET:chosen=trial
    chosen.sort(key=lambda x:(x['session_number'],x['utterances'][0]['id']))
    return chosen,{'query_sha256':g.digest(query),'ranking':ranked,'selected_ids':
        [r['id'] for d in chosen for r in d['utterances']],'history_tokens':tokens(chosen),
        'budget':BUDGET,'algorithm':'0.65 cosine(qwen3.7 embedding)+0.25 normalized BM25 bigrams+0.10 relative recency; first/latest session best-pair anchors',
        'all_corpus_pairs':len(documents),'selected_pairs':len(chosen)}

def method_context(secret,public,method,node):
    context=condition_input(public,'H2_full')
    if method=='B1_full':return context,{'history_tokens':tokens(context['previous_session_dialogues']),'budget':None}
    context['previous_session_dialogues']=[]
    if method=='B2_temporal_RAG':
        memory,info=retrieve(secret,public)
        context['historical_memory']=memory
    else:
        number=public['current_session']-1
        snap=read(P2/'memory_snapshots'/method/public['case_id']/('S%02d.json'%number))
        memory=snap['state'];info={'history_tokens':tokens(memory),'budget':BUDGET,'memory_sha256':snap['state_sha256']}
        context['historical_memory']=memory
    write(P2/'method_inputs'/node/(method+'.json'),{'public_input_sha256':g.digest(public),'context':context,'memory_info':info})
    return context,info

def run_main(secret,only_available=False):
    if not read(P2/'empirical_gate.json')['passed']:raise ValueError('Empirical gate not passed')
    tasks=[];pending=[]
    for node,public in selected().items():
        for method in ('B1_full','B2_temporal_RAG','B3_generic_rollup','B4_psych_rollup'):
            snapshot=P2/'memory_snapshots'/method/public['case_id']/('S%02d.json'%(public['current_session']-1))
            if method in ('B3_generic_rollup','B4_psych_rollup') and not snapshot.exists():
                if only_available:pending.append({'node':node,'method':method});continue
                raise ValueError('Memory not complete: '+str(snapshot))
            context,info=method_context(secret,public,method,node)
            for sample in (1,2):tasks.append((node,public,method,context,info,sample))
    def one(t):
        node,public,method,context,info,sample=t
        path=P2/'method_answers'/('%s_%s_%d.json'%(node,method,sample))
        if method=='B1_full':
            source=P2/'reselection/answers'/('%s_H2_full_%d.json'%(node,sample))
            row=copy.deepcopy(read(source));row.update(method=method,reused_from=str(source.relative_to(ROOT)),memory_info=info)
            if row['request_sha256']!=g.digest(g.build_payload('counselor',PROMPT,config(),context)):
                raise ValueError('Full-context reuse would change task/config')
            write(path,row);return row
        return api_record(secret,PROMPT,context,path,'method_comparison',node=node,method=method,sample=sample,
                          public_input_sha256=g.digest(public),memory_info=info)
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(one,tasks))
    write(P2/'main_run_status.json',{'status':'partial' if pending else 'completed','answers':len(rows),
        'pending':pending,'new_calls':sum(not r.get('reused_from') for r in rows),
        'selected_nodes':list(selected()),'bottom_model':config()['model'],'memory_budget':BUDGET,
        'temporal_graph':'No existing implementation found; no claim of superiority over graph memory',
        'human_review_performed':False,'updated_at':g.now()})

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=('reselected_ablation','memory','main','main_available'))
    args=parser.parse_args();secret=os.environ.get('DASHSCOPE_API_KEY') or sys.stdin.readline().strip()
    if not secret:raise ValueError('API key required on stdin')
    os.environ['DASHSCOPE_API_KEY']=secret
    {'reselected_ablation':run_reselected_ablation,'memory':run_memory,'main':run_main,
     'main_available':lambda key:run_main(key,only_available=True)}[args.phase](secret)

if __name__=='__main__':main()
