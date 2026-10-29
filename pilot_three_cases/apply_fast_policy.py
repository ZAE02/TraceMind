"""Apply the user's explicit time/token-saving review policy; retain past reviews."""
from pathlib import Path
import generator as g

policy_path = g.ROOT / 'configs/review_policy.json'
old = g.read_json(policy_path)
g.write_json(g.ROOT / 'configs/revisions/review_policy_before_fast.json', old)
policy = dict(old, reviewer_model='gpt-6-luna', reasoning_effort='low', minimum_mean_score=3,
              final_independent_second_review=False, effective_at=g.now(),
              authorization='User explicitly requested lower subagent intelligence and fewer review restrictions to save time/tokens.',
              retained_previous_reviews=True,
              major_rejection_conditions=['material factual contradiction', 'private/future information leakage', 'severely inappropriate counseling'],
              checkpoint_trials='Qwen public-only feasibility trials; duplicate GPT trials cancelled')
g.write_json(policy_path, policy)
p = g.ROOT / 'final_audit.py'
s = p.read_text(encoding='utf-8')
start = s.index('            second_path =')
end = s.index('            report["sessions"].append', start)
s = s[:start] + '            second_scores = {}\n            combined_mean = None  # User cancelled mandatory duplicate review.\n' + s[end:]
p.write_text(s, encoding='utf-8')
p = g.ROOT / 'finalize_documents.py'
s = p.read_text(encoding='utf-8')
s = s.replace('GPT-6.1 Sol 高强度模型审阅', '前期 GPT-6.1 Sol/high、后续 GPT-6 Luna/low 单次模型审阅')
s = s.replace('由 GPT-6.1 Sol 高强度子 Agent 审阅', '由模型子 Agent 审阅；前期 Sol/high、后续 Luna/low')
s = s.replace('"reviewer_model": "gpt-6.1-sol", "reasoning_effort": "high", "sessions": model_rows', '"reviewers": ["gpt-6.1-sol/high", "gpt-6-luna/low"], "sessions": model_rows')
s = s.replace('"- [第二次独立审阅](second_reviews/)单独保存，具体结论以其文件为准。",', '"- 用户要求节省时间和token后，取消强制第二次复审与重复GPT试答；未完成材料仅为草稿。",')
s = s.replace('指定模型逐会话主审及各案例总结；`second_reviews/`：另一个独立子Agent复审。', '逐会话主审及各案例总结；实际模型与推理强度见每份审阅。')
s = s.replace('逐会话模型均分≥4且无重大问题才续作。', '前期模型均分≥4；用户后续降低限制，后续 Luna/low 均分≥3且无重大问题即可续作，取消强制双模型复审。')
s = s.replace('reasoning low、max_output_tokens4096', '来访者 reasoning low，v8咨询师 medium（早期 low）；试答 low；max_output_tokens4096')
s = s.replace('"review_model": "gpt-6.1-sol",\n              "review_reasoning_effort": "high"', '"review_models": ["gpt-6.1-sol/high", "gpt-6-luna/low"],\n              "review_policy": "single reviewer; user-authorized relaxed gate"')
s = s.replace('"scores": values, "mean_score":', '"reviewer_model": review["reviewer_model"], "reasoning_effort": review["reasoning_effort"],\n                               "scores": values, "mean_score":')
p.write_text(s, encoding='utf-8')
print('Fast review policy applied; prior reviews retained.')
