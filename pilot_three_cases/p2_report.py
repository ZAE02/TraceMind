# -*- coding: utf-8 -*-
"""Render descriptive P2 reports from validated scores; do not infer significance."""
import collections
import json
from p2_validation import P2,read

LABELS={'B1_full':'Full Context','B2_temporal_RAG':'Temporal RAG','B3_generic_rollup':'Rolling Summary','B4_psych_rollup':'Psychological Tracking','M0':'完整历史，无附加记忆','M_old':'完整历史＋早期日期记忆','M_updated':'完整历史＋更新日期记忆'}

def table(rows,branch=False):
    dims=('mean_clarification_quality','mean_history_use','leading_or_unsupported_count','fact_conflict_count') if branch else ('mean_U','mean_R','mean_E','P_count')
    head=['节点','组','样本数']+[{'mean_U':'U','mean_R':'R','mean_E':'E','P_count':'P数量','mean_clarification_quality':'澄清','mean_history_use':'历史使用','leading_or_unsupported_count':'无依据限定数量','fact_conflict_count':'冲突数量'}[d] for d in dims]
    lines=['| '+' | '.join(head)+' |','| '+' | '.join(['---']*len(head))+' |']
    for r in rows:
        values=[r['node'],LABELS.get(r['group'],r['group']),str(r['samples'])]+['%.2f'%r[d] if d.startswith('mean') else str(r[d]) for d in dims]
        lines.append('| '+' | '.join(values)+' |')
    return '\n'.join(lines)

def main():
    history=read(P2/'scored_history_answers.json')['answers'];gate=read(P2/'empirical_gate.json')
    methods=read(P2/'results_main.json')['table'];probes=read(P2/'results_probe.json')['table'];branches=read(P2/'results_branch.json')['table']
    aggregate=[]
    for group in ('B1_full','B2_temporal_RAG','B3_generic_rollup','B4_psych_rollup'):
        rows=[r for r in read(P2/'scored_main.json')['rows'] if r['method']==group]
        aggregate.append({'node':'三个节点合计','group':group,'samples':len(rows),'mean_U':sum(r['U'] for r in rows)/len(rows),'mean_R':sum(r['R'] for r in rows)/len(rows),'mean_E':sum(r['E'] for r in rows)/len(rows),'P_count':sum(r['P'] for r in rows)})
    tracking=next(r for r in aggregate if r['group']=='B4_psych_rollup');rag=next(r for r in aggregate if r['group']=='B2_temporal_RAG');generic=next(r for r in aggregate if r['group']=='B3_generic_rollup')
    original_h2=[r for r in history if r['scope']=='reselected' and r['condition']=='H2_full']
    new_full=next(r for r in aggregate if r['group']=='B1_full')
    prior_h2_R=sum(r['R'] for r in original_h2)/len(original_h2)
    errors=[r for r in history if r['P']];original=sum(r['scope']=='original' for r in errors);new=sum(r['scope']=='reselected' for r in errors)
    scoped=[r for r in errors if r['condition']=='H2_full']
    text=f'''# P2 小规模问题验证报告

> 三个人物、24次拟真合成会话；模型辅助评审，未进行人工或临床审核。完成时间以接口日志和审计时间为准。

## 主要结果

1. 原9个节点的两名审阅者共同认可历史必要性的节点为 **0**，原门槛失败。按方案从既有会话重截6个候选，5个获共同认可，按每案例最新认可节点的固定选择规则选出 N01、N04、N06；新审阅者在阅读消融答卷前独立确认这3个节点。筛选是探索性的，不能称为独立测试集或临床验证。
2. 重截3节点的完整历史相对当前会话 R 差值分别为 **{gate['effects'][0]['delta_R']:+.2f}、{gate['effects'][1]['delta_R']:+.2f}、{gate['effects'][2]['delta_R']:+.2f}**（0–4分）。U变化并非全部为正。历史能影响具体探索，但不保证心理理解一定更准确。
3. 原54份消融答卷中 P={original}，重截18份中 P={new}；其中完整历史条件下共 {len(scoped)} 份。这些是广义无依据心理预设，不能全部算作“沿用早期心理解释”。C02_CP3/H2/sample1 提供了旧自我审判框架限定当下现实权衡的候选实例；C03_CP1/H2/sample2 则是把意外限定为两种“应该知道”的标准，历史解释来源仍有歧义。现阶段支持**存在候选错误实例**，尚不足以证明其普遍性或由检索重建导致。
4. 方法对照中，Psychological Tracking 相对 Temporal RAG 的平均 U/R 差值为 **{tracking['mean_U']-rag['mean_U']:+.2f}/{tracking['mean_R']-rag['mean_R']:+.2f}**，相对 Rolling Summary 为 **{tracking['mean_U']-generic['mean_U']:+.2f}/{tracking['mean_R']-generic['mean_R']:+.2f}**。这些差值是3个节点、每组每节点2样本的描述性观察；逐节点与错误数量见下表，不能据此宣称优于RAG或图记忆。

## 已完成规模

| 工作 | 规模 |
| --- | --- |
| 原节点审计与历史消融 | 9节点、54答卷 |
| 重截候选与重新消融 | 6候选、选3节点、18答卷 |
| 主要方法对照 | 3节点×4组×2样本＝24答卷，6份Full Context复用消融输出 |
| 日期记忆机制探针 | 2节点×3条件×2样本＝12答卷，4份无记忆条件复用 |
| 独立模拟互动 | 2节点×3组×2重复＝12条支线，每条2组往返，36次新增角色调用 |
| 审阅 | 两名独立GPT-6.1 Sol/medium；重要分歧第三名模型裁决；人工字段空白 |

## 方法对照

{table(aggregate)}

{table(methods)}

B1提供所有实际历史；B2对实际对话对使用密集向量、中文bigram BM25及时间权重混合检索，保留首次/最近会话锚点与说话者；B3/B4均仅用上一份有界记忆加新完成会话逐次更新。后三组历史表示统一上限3000本地token，当前会话全文一致，底座qwen3.8-27b及任务、采样参数一致。B3允许保留心理感受和变化；B4额外维护解释的证据、适用范围、冲突与不确定性。未发现现成Temporal Graph实现，本轮未测图记忆。

主要对照全部24份最终P=0，未在这些节点上重现稳定的历史预设错误。逐点R：N04追踪与RAG相同，N06为RAG更高。更需要注意：Full Context复用同一批6份H2文本，但新的独立盲评平均R由消融阶段的 {prior_h2_R:.2f} 变为 {new_full['mean_R']:.2f}，评分波动大于追踪与RAG的平均差值。两个评分批次保持分开，不为对齐结果修改分数；当前模型评分稳定性不足以支持细小优势。

## 日期记忆探针

{table(probes)}

三条件都得到同一完整历史。早期和更新记忆均标明形成阶段及暂定性；更新记忆只使用冻结之前的当前会话，不输入后文或Gold。早期/更新记忆长度未严格匹配，额外提示及信息量是混杂因素。该探针用于检查旧解释的影响，不进入方法排名，也不能证明RAG的失败机制。

早期记忆条件4份回答均P=0，没有重现“旧记忆必然诱发预设”。相反，更新记忆条件的N04/sample1被两名审阅者一致判为P=1：实际话语称“还没到你想打开它的时候”，把反复谈某体验解释为尚未想探索/准备不足，未经询问。输出还夸大了出现次数与“每次会话尾声”模式。这是显式记忆或新增解释也可能产生不当心理化的反例，不能作为持续更新必然改善的证据。

## 两组往返后的探索

{table(branches,True)}

统一采用各组第1份初始答卷，选择在互动结果产生前固定。来访者每节点使用同一冻结私有状态，能不同意咨询师；后续咨询师仅接收实际分支对话与该方法允许的历史。没有拼接原案例冻结后的来访者回答。初始回复复用、2条新来访者回复及1条新咨询师回复组成每支线。新增线索、迎合风险和理由保存在 `scored_branch.json`，完整短对话见 `interactive_branches/*/dialogue.md`。模拟器同属Qwen且材料为合成数据，不能将模拟认可当作咨询理解的真实验证。

## 完整性与过程修正

- 原始会话和9节点保留；重截点来自原文，不改写来访者以制造错误。
- 原消融审阅包曾有同案例节点乱序，存在后节点影响前节点判断的风险；对受影响的R1两节点、R2四节点进行重新盲评，保留旧评分、风险记录和新裁决。重截确认、主要对照与探针各只用同案例一个节点，避免同组内未来节点先读。
- 一个完整历史答卷JSON无效，原文保留，未再生成选更好答案；实际咨询字符串可独立解码，评分标记格式异常。接口截断不能当成功答卷使用。
- 普通与心理记忆均允许语法层面JSON修复，检查条目数、证据ID保持不变，原文与修复日志保留；3000token裁剪保留删除条目。记忆失败请求和额外恢复额度记录在 `memory_processing_policy.json` 与接口日志，所有比较咨询答卷仍为4096输出上限。故这不是严格等总算力实验。
- N04模拟状态去掉未发生的“第二天做完/无争吵”及披露安排指令；既往事件改用实际来访者报告以避开原规划卡日期冲突。排除记录保留。
- 最终请求权限、冻结哈希、链式记忆、分支出处与凭据扫描见 `final_integrity_audit.json`；接口用量含失败尝试见 `resource_usage.json`，不估计子Agent用量或人民币成本。

## 能回答与仍不能回答的问题

**RQ1：现有Agent会不会带着未经核实的心理预设回应？** 本小样本确有被模型评审判为P的实例，但只有部分与既往框架有关，且暂定假设与引导性限定的边界存在判断分歧。需要独立人工复核候选历史实例，不能把全部P计为历史固化，也不能证明检索特有问题。

**RQ2：持续维护心理理解是否比检索重建更有利？** 当前只获得上表描述性差异；节点经筛选、每条件2次采样、三个合成人物以及模型评分都限制结论。保留强RAG与普通摘要表现，不据论文预期挑选结果。当前原型还不是最终论文Method。

建议先人工复核具体错误、负例与模拟迎合，再用独立节点冻结筛选和评分规则。规模扩大之前不开展训练，不宣称统计普遍性、临床有效性或图结构方法优势。
'''
    (P2/'pilot_validation_report.md').write_text(text,encoding='utf-8')
    failures=read(P2/'failure_sources.json');lines=['# 错误实例与反例','', '> 全部为拟真数据和模型辅助判断；P不自动代表历史固化。','']
    categories={'C01_CP2':'仅当前发言条件的因果/顺序预设，不能是历史携带证据。','C02_CP3':'历史框架过度延伸的候选：旧自我审判解释压缩当前现实任务权衡；不是检索导致的证据。','C03_CP1':'完整历史下无依据的二选一限定；与早期心理解释的直接联系尚不确定。','C03_CP3':'当前会话条件把正常状态澄清心理化；“好像/问号”与引导预设的边界需人工复核。','N04':'仅当前发言把尚不确定的体验称作已经松下来；不属于历史携带。'}
    for row in failures:
        lines+=['## '+row['node']+' / '+row['condition']+' / sample'+str(row['sample']),'',categories[row['node']],'','> '+row['utterance'],'','R1：'+row['R1'],'','R2：'+row['R2'],'']
        if row['adjudication']:lines+=['第三模型裁决：'+row['adjudication']['reason'],'']
    for row in read(P2/'probe_failure_sources.json'):
        lines+=['## 日期记忆探针：'+row['meta']['node']+' / '+row['meta']['condition']+' / sample'+str(row['meta']['sample']),'', '更新记忆条件下出现的反例；不是早期记忆或RAG特有的错误。','', '> '+row['answer']['next_counselor_utterance'],'','R1：'+row['R1']['reason'],'','R2：'+row['R2']['reason'],'']
    lines+=['## 负例与合理探索','', '主要对照和机制探针的全部非错误回答亦保留；谨慎提出旧理解作为可否认的假设不计P。若追踪方法只泛问、过度分解来访者措辞或忽略现实事件，即使P=0也应降低R。请结合 results_main.csv、results_probe.csv 及盲评理由选取负例，勿仅展示P=1。','', '具体历史链：C02 S7 的自我审判线索 → S8-U01/U03 的边界行动、现实报表与有限内疚 → H2/sample1“报表只是把判词…”的限定。冻结来源见原 C02_CP3；完整上下文判断不能替代人工确认。']
    (P2/'failure_examples.md').write_text('\n'.join(lines),encoding='utf-8')
    decision=f'''# 下一轮决策

本轮已完成问题验证、四方法对照、日期记忆探针与12支线。原节点门槛失败及重截过程保留；未新增案例、未训练。

- 历史价值：三个重截节点均有追问变化，值得保留纵向任务；U并非一律随历史改善。
- 历史预设：C02_CP3/H2/sample1是优先人工复核的候选；另保留仅当前发言的P例说明错误不必来自历史。
- 方法动机：心理追踪比Temporal RAG的平均U/R差值为{tracking['mean_U']-rag['mean_U']:+.2f}/{tracking['mean_R']-rag['mean_R']:+.2f}。该小规模结果不能支持普遍领先结论；结合逐节点表现与普通摘要，不确定论文最终方法。
- 评分稳定性：同一批完整历史答卷的新盲评平均R从{prior_h2_R:.2f}变成{new_full['mean_R']:.2f}，变动大于方法差值。下一轮优先验证评审规程与人工复核，不把小分差作为扩大方法研究的依据。
- 下一步先让独立规划者或人工看报告、错误/负例、匿名答卷和互动迎合风险；冻结独立验证节点与评分规程，再决定是否扩大现象验证。若强RAG表现相近，就调整动机并保留结果。

暂不新增案例或训练，不为得到显式心理建模优势而削弱RAG、不把模型审核写成人工通过。
'''
    (P2/'next_iteration_decision.md').write_text(decision,encoding='utf-8');print('Reports rendered from final validated scores.')

if __name__=='__main__':main()
