# P1：现象分析与机制对照

从 [P1评测报告](P1评测报告.md) 阅读结论，再查看 `analysis/` 的原始分数、四张图和案例切片。本目录是独立实验；`frozen_p0/` 是 P0 相关原始文件的冻结副本，`frozen_manifest.json` 记录 425 个 SHA-256。不要将 `internal/condition_key.json`、`mechanisms/internal_blind_key.json`、`budget_match/internal_blind_key.json` 或 `full_access/internal_blind_key.json` 交给盲评者。

`validated_annotations/` 是 Qwen Flash 基于冻结前公开对话对 P0 草稿的模型复核；`reviews/` 和 `second_reviews/` 保存 C0–C3 的逐答卷评分。`mechanisms/` 保存 C4/C5 的形成过程、原始回应和盲评；`budget_match/` 保存 1,200 字符响应记录上限的固定裁剪对照；`full_access/` 保存 C4-full 同完整历史对照。评审模型为 `qwen3.7-flash-2026-07-15`，与答题模型 `deepseek-v4-pro-0813` 不同。所有数据均为拟真合成材料，未有人类或临床评分。

Python 3.9 以上，脚本只用标准库。模型调用从当前进程环境变量 `DASHSCOPE_API_KEY` 或 stdin 读取密钥；不写入文件。已保存的结果按节点跳过，适合断点续跑。执行顺序：

```powershell
python p1.py freeze
python p1.py annotations
python p1.py review
python p1.py second_review
python analyze.py
python mechanisms.py rolling
python mechanisms.py c4
python mechanisms.py responses
python judge_mechanisms.py prepare
python judge_mechanisms.py review
python budget_match.py compress
python budget_match.py answers
python budget_match.py prepare
python budget_match.py review
python full_access.py formulations
python full_access.py answers
python full_access.py prepare
python full_access.py review
python analyze_mechanisms.py
python audit_p1.py
```

脚本中的路径均以本目录为根；报告的结论仅限这批 8 个独立来访者。所有区间按案例聚类，未使用把检查点当独立样本的标准误。预算对照的裁剪会删除记录内容，不能用于证明相同信息下的机制优劣；同完整历史对照是主要的访问权限敏感性检查。
