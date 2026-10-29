# P0：心理理解如何用于继续咨询

本目录是独立的拟真合成实验。完整研究思路见 [研究思路与实验方案](研究思路与实验方案.md)。计划生成 8 个虚构来访者、64 次会话、24 个 S3/S5/S7 检查点，并在四种历史条件下分别回答心理理解与下一轮咨询回应两个任务。旧项目仅用于实现参考，旧案例不混入本数据。

本次执行的完成数量、用量及限制见 [P0运行报告](P0运行报告.md)。

`plans/` 是研究者私有的人物与事件计划；`dialogues/` 是逐条生成的双向咨询记录；`checkpoints/public/` 是可见冻结输入；`checkpoints/private/` 含私有设定及冻结后的原对话，禁止进入评测；`checkpoints/annotation_drafts/` 仅根据公开历史生成、尚未经人工核验。`retrieval/` 保存向量检索和排序，`inputs/` 保存实际条件输入，`answers/` 保存原始答卷。`review/blank_*` 是未填写评审文件，`review/internal_blind_key.json` 应与盲评材料隔离。

生成与答题模型为 `deepseek-v4-pro-0813`；仅向量检索使用 `qwen3.7-text-embedding-flash`。接口为阿里云北京兼容模式的 Responses 和 Embeddings。密钥通过当前进程的 `DASHSCOPE_API_KEY` 或 stdin 传入，不写文件。请求非流式、`store=false`，成功结果逐条落盘，重跑时跳过已完成结果；`logs/calls.jsonl` 只保存请求哈希、状态和用量，不保存认证头。运行顺序：

```powershell
python run_p0.py all --workers 4
python run_p0.py audit
```

每个 Session 固定 8 个咨询往返，处于原方案允许的 8–12 范围。冻结点是 S3、S5、S7 的第 5 次来访者发言。C0 仅当前会话；C1 从历史咨询往返中按向量相似度选取；C2 先保障最早和最近阶段各至少一个片段，再按相似度补足；C3 使用全部既往会话。C1/C2 使用相同的 4500 字符历史片段预算。Task A 和 Task B 各独立请求。

按用户选择，本轮跳过人工对话审查和盲评，也不以模型评分代替。`audit.json` 只核查结构、可见证据引用和输入边界，并汇总调用用量；它不能证明咨询真实性、标注充分性、错误现象可重复或方法优劣。没有人工评审时，原方案的 80% 可评测性门槛无法判断。
