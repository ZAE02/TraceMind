import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import generator

def save(cp, answer, deviation=None):
    public = generator.read_json(ROOT / 'checkpoints/public' / ('C03_' + cp + '.json'))
    record = {'kind': 'model_answer', 'synthetic': True, 'status': 'completed',
              'case_id': 'C03', 'checkpoint_id': cp,
              'model_requested': 'gpt-6.1-sol', 'reasoning_effort': 'high',
              'execution_source': 'Codex collaboration subagent', 'source_agent': '/root/independent_c03',
              'public_input_sha256': generator.digest(public), 'created_at': generator.now(),
              'annotation_visible': False, 'private_facts_visible': False,
              'raw_text': json.dumps(answer, ensure_ascii=False), 'parsed_answer': answer, 'format_valid': True}
    if deviation:
        record['execution_deviation'] = deviation
    generator.write_json(ROOT / 'audit/smoke_answers/gpt-6.1-sol' / ('C03_' + cp + '.json'), record)
    print('Saved', cp, record['public_input_sha256'])

save('CP1', {
    'understanding': '公开对话显示，接口排查耗时曾让他难以求助；问题解决后，他仍用自己两天、老韩二十分钟的比较评价自己“笨”。本次在共同联调中更容易顺口提问，事后自我评价也较轻；最新事件是老韩承认自己也记不清，两人共同查文档，而他对此感到意外。可以暂时推测，他对老韩应当知道什么、以及自己不知道意味着什么的期待可能值得探索，但目前不能认定这些期待的具体内容或来源，也不能认定已有稳定改变。',
    'uncertainties': ['听到老韩也记不清时，他原本期待对方怎样回应，意外具体指向什么？', '这个瞬间是否改变了他对自己没弄懂字段的评价，还是只是对老韩的意外？', '此次共同讨论与此前独自排查、单独求助的不同，哪些条件让提问较容易，能否延续尚未知。'],
    'next_response': '上次他二十分钟找出问题，你回头会觉得自己早该想到；这次听到他也记不清，你反而愣了一下。那一刻你原本以为他会怎么回应，听到这句话后，你对自己没确定这个字段的感觉有没有变化？',
    'evidence_ids': ['C03-S01-U15', 'C03-S01-U17', 'C03-S02-U03', 'C03-S02-U09', 'C03-S02-U15', 'C03-S03-U03', 'C03-S03-U05', 'C03-S03-U09']
}, 'CP1 answer was fully authored before CP2 was read. Its first save failed because PowerShell stdin used a non-UTF-8 encoding; the following command nevertheless displayed CP2. This record preserves the already authored CP1 answer verbatim, but CP1 persistence occurred after CP2 exposure. Strict save-before-read ordering was therefore not fully satisfied; no annotations or private facts had been read.')

save('CP2', {
    'understanding': '公开历史呈现几个情境中的变化：最初排查两天后求助感到难堪并事后贬低自己，后来在联调中顺口问、主动带着初步了解问新组件、本周向陆工确认已有方案，都有斟酌但较少卡住。最新这次问题在文档中已有答案且他没找到，仍能问出来；“没卡住”是行为事实，不能因此断言没有羞耻或自我评价。他较早说明对没搞定的自我评价，后续又说明老韩也不确定会让自己没底，因此需分辨本次是否仍有这些体验及为什么能继续开口。',
    'uncertainties': ['这次斟酌开口时具体想到了什么，问题在文档中已有答案对他意味着什么？', '他最后怎么问出口，哪些情境或判断使斟酌没有变成拖延？', '同事指明文档位置后，当下及事后是否有自我评价，不能从“没卡住”推定没有。', '多个较容易提问的事件是否形成稳定变化，遇到模糊问题时如何反应尚未知。'],
    'next_response': '以前查了两天没解决时，你会觉得开口得先交代一下；这次答案就在文档里，你也斟酌了，却还是问出来了。能说说开口前你在想什么，后来怎么决定问的？',
    'evidence_ids': ['C03-S01-U15', 'C03-S01-U17', 'C03-S02-U15', 'C03-S03-U15', 'C03-S04-U05', 'C03-S04-U13', 'C03-S05-U03', 'C03-S05-U05', 'C03-S05-U11']
})
