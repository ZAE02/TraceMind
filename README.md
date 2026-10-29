# TraceMind：长期咨询中的心理理解与回应评测

本仓库保存一组**合成咨询对话**上的探索性实验，研究跨会话历史、按需重建和滚动维护的心理理解记录如何影响模型的理解与下一轮回应。数据不来自真实来访者；模型评分未经人工或临床校准，不能用于临床判断。

| 目录 | 内容 |
| --- | --- |
| [`pilot_three_cases/`](pilot_three_cases/) | 三例早期纵向实验及生成、审计脚本 |
| [`p0_understanding_in_action/`](p0_understanding_in_action/) | 8 例、64 次会话、24 个检查点的现象实验 |
| [`p1_evaluation/`](p1_evaluation/) | DeepSeek Pro 作答、Qwen Flash 评审的 P1 分析 |
| [`p1_1_qwen_rerun/`](p1_1_qwen_rerun/) | Qwen Flash 作答、Qwen Max 评审的独立复测 |

建议先阅读各目录的 `README.md` 和报告，再查看保存的答卷、评分与审计记录。两个 P1 版本都**没有证明**滚动维护优于从完整历史按需重建；新旧版本同时更换了作答和评审模型，不能将分数变化归因于单一因素。

脚本通过运行环境中的 `DASHSCOPE_API_KEY` 读取密钥。请勿将密钥、真实来访者资料或其他私人数据提交到仓库。
