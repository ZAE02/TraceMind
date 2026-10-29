"""P2 pilot: freeze audit and public-only history ablation.

Human judgments are intentionally left blank. This module never reads a
research plan, private checkpoint state, or annotation while making a model
request. Run `prepare`, then `ablation` with an API key on stdin.
"""
import argparse
import csv
import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import generator as g

ROOT = g.ROOT
P2 = ROOT / 'p2_validation'
PROMPT = """你是一位进行拟真咨询演练的咨询师。依据给定的可见对话，先简短说明目前有证据支持的心理理解，并区分仍未知的部分，然后写出下一轮实际会说的1–3句。利用可见证据提出具体探索；不要把旧推断或咨询师先前的解释说成来访者确认的事实，不做诊断。只输出JSON对象，字段严格为 supported_understanding（数组，每项含claim、evidence_ids、certainty，certainty为tentative/moderate/strong）、remaining_uncertainties（字符串数组）、next_focus（字符串）、next_counselor_utterance（字符串）。只引用输入中可见发言的id，无历史可留空。没有唯一标准回复。"""

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write(path, value):
    g.write_json(path, value)

def visible(rows):
    return [{'id': row['id'], 'role': row['role'], 'text': row['text']} for row in rows]

def expected_public(case_id, number, freeze_id):
    previous = []
    for n in range(1, number):
        data = read(ROOT / 'dialogues' / case_id / ('S%02d.json' % n))
        previous.append({'session_number': n, 'utterances': visible(data['utterances'])})
    rows = visible(read(ROOT / 'dialogues' / case_id / ('S%02d.json' % number))['utterances'])
    matches = [i for i, row in enumerate(rows) if row['id'] == freeze_id and row['role'] == 'client']
    if len(matches) != 1:
        raise ValueError('Freeze ID invalid: ' + freeze_id)
    index = matches[0]
    return previous, rows[:index], rows[index]

def prepare():
    P2.mkdir(exist_ok=True)
    rows = []
    reference = []
    warnings = []
    snapshots = {}
    for path in sorted((ROOT / 'checkpoints/public').glob('*.json')):
        public = read(path)
        cid = public['case_id']; cp = public['checkpoint_id']; number = public['current_session']
        name = cid + '_' + cp
        previous, prefix, latest = expected_public(cid, number, public['current_user_utterance']['id'])
        if (previous != public['previous_session_dialogues'] or prefix != public['current_session_dialogue']
                or latest != public['current_user_utterance']):
            raise ValueError('Public freeze does not match actual dialogue: ' + name)
        annotation = read(ROOT / 'checkpoints/annotations' / (name + '.json'))
        if annotation['public_input_sha256'] != g.digest(public):
            raise ValueError('Public hash mismatch: ' + name)
        available = {r['id']: r for s in previous for r in s['utterances']}
        available.update({r['id']: r for r in prefix + [latest]})
        # The previous annotation is a research aid, never an evaluation truth or prompt.
        import export_checkpoints as ex
        cited = ex.evidence_ids(annotation['annotation'])
        if any(item not in available for item in cited):
            raise ValueError('Annotation refers to future/unavailable evidence: ' + name)
        source_map = [{'id': item, 'role': available[item]['role'],
                       'session': int(item.split('-S')[1].split('-')[0]),
                       'text': available[item]['text']} for item in sorted(set(cited))]
        reference.append({'node': name, 'source': 'prior_model_annotation_not_human_gold',
                          'annotation_sha256': g.digest(annotation), 'cited_visible_evidence': source_map,
                          'annotation': annotation['annotation']})
        snapshots[name] = {'public_input_sha256': g.digest(public),
                           'current_utterance_id': latest['id'], 'session': number,
                           'previous_sessions': len(previous), 'current_prefix_utterances': len(prefix),
                           'annotation_sha256': g.digest(annotation)}
        rows.append({'node': name, 'current_utterance_id': latest['id'],
                     'public_input_sha256': g.digest(public), 'human_reviewer_1': '', 'human_reviewer_2': '',
                     'needs_history': '', 'old_interpretation': '', 'new_evidence': '',
                     'uncertainty': '', 'response_target': '', 'valid_alternatives': '',
                     'failure_example': '', 'cue_leakage': '', 'adjudication': ''})
        if name == 'C03_CP3':
            warnings.append((name, '高风险：当前发言已说熟悉模块、有线索、想先验证，且否认卡住。'))
        elif name == 'C02_CP2':
            warnings.append((name, '高风险：当前发言已经直接区分担心自己先下判断与担心赵哥评价。'))
        elif name == 'C02_CP3':
            warnings.append((name, '当前发言直接说明内疚、工作未完成和对同事的具体担心；需检验历史是否改变回应。'))
    if len(rows) != 9:
        raise ValueError('Expected nine frozen nodes')
    write(P2 / 'frozen_manifest.json', {'created_at': g.now(), 'status': 'verified_against_canonical_dialogue',
        'synthetic': True, 'nodes': snapshots, 'source_plan': 'C:/Users/99291/Downloads/下一步执行方案.md'})
    write(P2 / 'gold_evidence_map.json', {'notice': 'Researcher reference from earlier model annotation; not human Gold and never included in model requests.',
                                         'nodes': reference})
    with (P2 / 'node_audit.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    docs = ['# 节点线索泄漏预审', '', '依据公开最新发言进行的初步筛查，**不是两名人工审阅者的结论**。',
            '所有9个节点的说话者、冻结位置、公开哈希与证据引用已核对。', '']
    docs += ['- **%s**：%s' % item for item in warnings]
    docs += ['', '其余节点也需人工比较 H1 与 H2；未列出不代表历史必需。',
             '现有9个Qwen试答只验证输入和格式，不能充当这一轮的人工判分。', '']
    (P2 / 'leakage_report.md').write_text('\n'.join(docs), encoding='utf-8')
    formdir = P2 / 'human_node_forms'; formdir.mkdir(exist_ok=True)
    for row in rows:
        for reviewer in (1,2):
            name = row['node']
            text = ['# %s · 人工审阅者 %d' % (name, reviewer), '',
                    '仅看公开材料：[冻结输入](../../checkpoints/public/%s.json)。先独立填写，再讨论。' % name,
                    '这是拟真合成材料；既有模型标注仅在独立判断后核对。', '']
            for key in ('needs_history','old_interpretation','new_evidence','uncertainty',
                        'response_target','valid_alternatives','failure_example','cue_leakage'):
                text += ['## ' + key, '', '待人工填写。', '']
            (formdir / ('%s_R%d.md' % (name, reviewer))).write_text('\n'.join(text), encoding='utf-8')
    print('P2 prepare: 9 frozen nodes verified; human judgments remain blank.', flush=True)

def condition_input(public, condition):
    if condition not in ('H0_utterance', 'H1_session', 'H2_full'):
        raise ValueError(condition)
    return {'session_background': public['session_background'],
            'previous_session_dialogues': public['previous_session_dialogues'] if condition == 'H2_full' else [],
            'current_session_dialogue': public['current_session_dialogue'] if condition != 'H0_utterance' else [],
            'current_user_utterance': public['current_user_utterance']}

_SAVE_LOCK = threading.Lock()

def run_ablation(secret):
    manifest = read(P2 / 'frozen_manifest.json')
    config = read(ROOT / 'configs/generation_config.json')
    active = dict(config, reasoning_effort='low', role_reasoning_effort={},
                  prompt_version='p2-history-ablation-v1', call_log_filename='p2_calls.jsonl')
    out = P2 / 'history_ablation.jsonl'
    existing = {}
    if out.exists():
        for line in out.read_text(encoding='utf-8').splitlines():
            record = json.loads(line); key = (record['node'], record['condition'], record['sample'])
            if key in existing: raise ValueError('Duplicate ablation key: ' + str(key))
            existing[key] = record
    tasks = []
    for path in sorted((ROOT / 'checkpoints/public').glob('*.json')):
        public = read(path); name = path.stem
        if g.digest(public) != manifest['nodes'][name]['public_input_sha256']:
            raise ValueError('Frozen public input changed: ' + name)
        for condition in ('H0_utterance','H1_session','H2_full'):
            context = condition_input(public, condition)
            payload = g.build_payload('counselor', PROMPT, active, context)
            if payload.get('store') is not False or payload.get('stream') is not False:
                raise ValueError('Unsafe shared state/stream')
            fingerprint = g.digest(payload)
            for sample in (1,2):
                key = (name, condition, sample)
                saved = existing.get(key)
                if saved:
                    if saved['request_sha256'] != fingerprint or saved['status'] != 'completed':
                        raise ValueError('Saved answer has changed request/incomplete result: ' + str(key))
                    continue
                tasks.append((name, condition, sample, context, payload, fingerprint))
    def one(task):
        name, condition, sample, context, payload, fingerprint = task
        text, call_id, response = g.call_api(ROOT, active, payload,
            {'purpose':'p2_history_ablation', 'node':name, 'condition':condition, 'sample':sample,
             'request_sha256':fingerprint, 'public_input_sha256':manifest['nodes'][name]['public_input_sha256']}, secret)
        try:
            cleaned = text.strip()
            if cleaned.startswith('```json'): cleaned = cleaned[7:].rsplit('```',1)[0]
            elif cleaned.startswith('```'): cleaned = cleaned[3:].rsplit('```',1)[0]
            parsed = json.loads(cleaned)
        except ValueError:
            parsed = None
        ids = {context['current_user_utterance']['id']}
        ids.update(r['id'] for r in context['current_session_dialogue'])
        ids.update(r['id'] for s in context['previous_session_dialogues'] for r in s['utterances'])
        cited = [id_ for item in (parsed.get('supported_understanding',[]) if isinstance(parsed,dict) else [])
                 if isinstance(item,dict) for id_ in item.get('evidence_ids',[])]
        valid = isinstance(parsed,dict) and all(k in parsed for k in
            ('supported_understanding','remaining_uncertainties','next_focus','next_counselor_utterance'))
        record = {'node':name,'condition':condition,'sample':sample,'status':'completed',
                  'request_sha256':fingerprint,'public_input_sha256':manifest['nodes'][name]['public_input_sha256'],
                  'call_id':call_id,'model_returned':response.get('model'),'created_at':g.now(),
                  'raw_text':text,'parsed_answer':parsed,'format_valid':valid,
                  'visible_evidence_ids_valid':all(id_ in ids for id_ in cited),
                  'visible_evidence_ids':sorted(ids), 'annotation_visible':False,'private_state_visible':False}
        with _SAVE_LOCK:
            with out.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(record,ensure_ascii=False)+'\n'); stream.flush()
        print('%s %s sample%d saved' % (name,condition,sample),flush=True)
    # No server-side state is used. Each request contains its full allowed context.
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(one, task) for task in tasks]
        errors = []
        for future in as_completed(futures):
            try: future.result()
            except Exception as exc: errors.append(str(exc))
    if errors:
        write(P2 / 'ablation_run_status.json', {'status':'partial','completed':len(existing)+len(tasks)-len(errors),
            'errors':errors,'updated_at':g.now()})
        raise RuntimeError('Some ablation requests failed; saved successes can resume')
    records = [json.loads(line) for line in out.read_text(encoding='utf-8').splitlines()]
    write(P2 / 'ablation_run_status.json', {'status':'completed' if len(records)==54 else 'partial',
        'answers':len(records),'format_valid':sum(r['format_valid'] for r in records),
        'citations_visible':sum(r['visible_evidence_ids_valid'] for r in records),
        'model_requested':config['model'],'temperature':active['temperature'],
        'reasoning_effort':'low','max_output_tokens':active['max_output_tokens'],
        'human_ratings_performed':False,'updated_at':g.now()})

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=('prepare','ablation'))
    args=parser.parse_args()
    if args.phase=='prepare': prepare(); return
    secret=sys.stdin.readline().strip()
    if not secret: raise RuntimeError('API key required on stdin')
    run_ablation(secret)

if __name__=='__main__': main()
