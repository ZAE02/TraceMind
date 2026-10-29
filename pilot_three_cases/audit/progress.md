# 当前生成进度

> 拟真合成材料；按用户授权采用GPT-6.1 Sol高强度模型审阅，人工分数为空。

更新：2026-10-13T06:10:19.557698+00:00。目标24次会话；当前完整24次、模型主审通过24次、已导出节点9个。

| 案例 | 会话 | 保存发言数 | 状态 |
| --- | --- | --- | --- |
| C01 | S01 | 24 | reviewed_by_model |
| C01 | S02 | 20 | reviewed_by_model |
| C01 | S03 | 20 | reviewed_by_model |
| C01 | S04 | 18 | reviewed_by_model |
| C01 | S05 | 20 | reviewed_by_model |
| C01 | S06 | 16 | reviewed_by_model |
| C01 | S07 | 22 | reviewed_by_model |
| C01 | S08 | 16 | reviewed_by_model |
| C02 | S01 | 20 | reviewed_by_model |
| C02 | S02 | 20 | reviewed_by_model |
| C02 | S03 | 20 | reviewed_by_model |
| C02 | S04 | 20 | reviewed_by_model |
| C02 | S05 | 20 | reviewed_by_model |
| C02 | S06 | 20 | reviewed_by_model |
| C02 | S07 | 20 | reviewed_by_model |
| C02 | S08 | 20 | reviewed_by_model |
| C03 | S01 | 20 | reviewed_by_model |
| C03 | S02 | 20 | reviewed_by_model |
| C03 | S03 | 20 | reviewed_by_model |
| C03 | S04 | 18 | reviewed_by_model |
| C03 | S05 | 20 | reviewed_by_model |
| C03 | S06 | 18 | reviewed_by_model |
| C03 | S07 | 20 | reviewed_by_model |
| C03 | S08 | 18 | reviewed_by_model |

状态reviewed_by_model代表指定模型主审通过；awaiting_model_review代表完整稿待审；generating/interrupted不算完成。

完整调用日志：`generation_logs/full_agent_calls.jsonl`；原稿、失败稿及中断稿：`revisions/`；配置变更：`audit/configuration_revisions.json`。
全部生成完成后再提取9个冻结节点、独立复审并试答。未开展训练或RAG对照。
