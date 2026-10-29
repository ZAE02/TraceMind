# P2：问题验证与对照实验

本目录使用已有三个拟真合成案例，未新增案例或训练。审阅由用户授权的两名独立 GPT-6.1 Sol / medium 子 Agent 执行；重要分歧交第三名模型裁决。人工字段保持空白。

## 阅读顺序

1. `pilot_validation_report.md`：完整结果、两项研究问题及限制。
2. `results_by_checkpoint.csv`：原九节点与三个重截节点的历史消融。
3. `results_main.csv`：四种历史利用方式；`results_probe.csv`：带日期记忆的机制探针。
4. `failure_examples.md`：具体错误与反例；`next_iteration_decision.md`：下一步建议。
5. `results_branch.csv` 与 `interactive_branches/*/dialogue.md`：两组往返的模拟互动。

## 数据与审计

`reselection/public/` 为重截节点的实际可见历史；原九节点冻结信息在 `frozen_manifest.json`。`gold_evidence_map.json` 是此前模型辅助标注，不能视为人工 Gold，且不进入咨询师输入。私有模拟状态在 `simulator_states/`，仅供来访者模拟器使用。`internal/*blind_key.json` 是盲包与方法的映射，应与审阅材料隔离。

原始答卷、格式无效答卷和失败请求均保留。`memory_snapshots/` 保存滚动摘要及心理理解的逐会话原文、修复记录、裁剪与链式哈希；`method_inputs/` 保存实际输入及检索排序。`final_integrity_audit.json` 检查信息权限、预算、冻结与调用出处；`resource_usage.json` 记录接口处理用量，不包含子 Agent 用量或估算费用。

## 运行

Python 3.9；本地依赖 `tokenizers==0.22.2`、`json-repair==0.44.1` 位于 `dependencies/`。公开 Qwen tokenizer 的来源与 SHA 位于 `tokenizer/source.json`，未下载模型权重。3000 token 是本地 tokenizer 测得的历史记忆预算，托管接口 tokenizer 是否完全一致未知。

密钥通过进程环境 `DASHSCOPE_API_KEY` 提供，也支持父进程 stdin 注入后存入子进程环境；不写文件或日志。

```powershell
python p2_methods.py reselected_ablation
python p2_methods.py memory
python p2_methods.py main
python p2_main_blind.py
python p2_branches.py probes
python p2_branches.py branches
python p2_final_reviews.py publish probe
python p2_final_reviews.py publish branch
# 独立审阅文件到齐后执行 collect；分歧裁决到齐后执行 finalize。
python p2_final_reviews.py collect main
python p2_final_reviews.py finalize main
python p2_final_audit.py
```

成功调用按请求哈希恢复，修改输入需新版本。所有比较答卷使用同一底座、通用任务、temperature 0.7、reasoning low、max_output_tokens 4096、store=false、非流式、完整显式角色输入。记忆更新的截断恢复额度及语法修复在 `memory_processing_policy.json` 单独记录；不将其额外处理成本混同为等算力比较。

## 评审口径

U（0–4）是当前心理理解的证据质量；R（0–4）是具体且不过度预设的咨询回应；E（0–2）是引用准确性；P（0/1）仅判断实际咨询话语是否把未确认心理原因当作事实并限制探索。谨慎探索旧假设不自动计错。S=true 仅表示严重不当咨询。机制探针不进入方法排名。

互动评分分别记录澄清、历史使用、无依据限定、事实冲突和模拟器迎合风险。模拟回应不是临床真值，也不能据此证明咨询有效。
