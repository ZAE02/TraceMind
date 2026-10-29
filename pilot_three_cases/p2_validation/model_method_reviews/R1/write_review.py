import json, hashlib
from pathlib import Path

def save(node, rows):
    base = Path(__file__).resolve().parents[2]
    packet = json.loads((base / 'blind_main' / 'R1' / (node + '.json')).read_text(encoding='utf-8-sig'))
    assert len(rows) == 8
    items = []
    for source, row in zip(packet['answers'], rows):
        u,r,p,e,reason,ids = row
        item = dict(anonymous_id=source['anonymous_id'], U=u,R=r,P=p,E=e,S=False,reason=reason,evidence_ids=ids)
        if not source['format_valid']:
            item['format_error'] = '原答卷JSON无效；直接按raw_text内容评分，未修复原文。'
        items.append(item)
    result = dict(kind='blind_model_answer_review',reviewer='R1',reviewer_model='gpt-6.1-sol',reasoning_effort='medium',human_review_performed=False,node=node,public_input_sha256=hashlib.sha256(json.dumps(packet['public_input'],sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest(),items=items)
    (Path(__file__).parent / (node+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__ == '__main__':
    save('N01', [
        (4,4,0,2,'准确保留事务并列与机制未知，回应当前叠加背景并具体询问此次体验及异同，未强加心理原因。',['C01-S03-U01','C01-S02-U15','C01-S01-U19']),
        (3,2,0,2,'事实与重复觉察有据；停顿说明问题有所过读。实际回应以两种未证实关联限定选择，探索略窄，但以问句提出可能性，未把心理原因确立为事实。',['C01-S03-U01','C01-S02-U11']),
        (4,3,0,2,'当前重复与负荷有据，母亲关联明确暂定；回应乱与又，但两个备选不在同一层面且加入当时已知又来的可能，具体性尚可。',['C01-S03-U01','C01-S01-U17','C01-S01-U18']),
        (3,4,0,2,'事务和再次过文件有据，自嘲的中等确定性超过文字证据；实际回应贴合本周并开放比较体验，没有将自嘲或原因写成事实。',['C01-S03-U01','C01-S02-U15']),
        (3,4,0,2,'按raw_text评分。重复、事务与母亲线有据，但本次不是模糊反馈或他人确认触发尚未确认；实际询问与上次体验异同，允许不同且回应当前。',['C01-S03-U01','C01-S02-U13','C01-S01-U15']),
        (4,3,0,2,'当前事实和未展开情绪区分准确；回应乱与重复觉察，问题稍抽象并收窄为两个方向，但无未证实心理原因断言。',['C01-S03-U01','C01-S02-U01','C01-S01-U15']),
        (4,4,0,2,'当前事务与体检结果均有据，未确认关联；实际定位晚上打开电脑的具体时点询问感受，开放且贴合当前。',['C01-S03-U01']),
        (4,4,0,2,'当前重复与多项事务理解有据，对母亲联系保持未知；回应背景并询问此回与此前感觉的异同，恰当具体。',['C01-S03-U01','C01-S02-U15'])
    ])
