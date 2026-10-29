# 心迹 TraceMind

动态心理理解记忆与多会话评测研究框架。系统将事件证据、心理状态、解释假设和待澄清问题分开维护，用支持与反驳证据修订认识，再评测对后续回应的影响。

**v0.2 状态：**20组场景计划已逐场个别化并通过独立模型审查；对话与正式实验仍在生成。最新实际数量见[运行状态](docs/status.json)。200组双人盲评待人工完成，不包含虚构人工评分或目标效果数字。案例均为虚构成人的合成材料。

```mermaid
flowchart LR
  D[隔离角色的数据生成] --> Q[PostgreSQL 任务队列]
  Q <--> A[活跃 Codex 会话中的 Terra / medium]
  Q --> E[公开发言与证据]
  E --> R[BGE-M3 + pgvector + BM25]
  R --> X[Cross-Encoder 重排]
  X --> G[LangGraph 检查点与恢复]
  G --> M[追加式理解版本]
  M --> C[4096 token 历史上下文]
  C --> T[理解 / 回应 / 两轮澄清]
  T --> J[匿名质量评审与访问归因]
  T --> H[双人独立人工评审]
```

| 子系统 | 代码（位于 `src/tracemind/`） | 职责 |
| --- | --- | --- |
| 领域与持久化 | `memory/`、`storage/` | 逐条证据、适用时间、支持/反驳、版本链与事务 |
| 检索 | `retrieval/` | 个案和时间过滤、向量/词项融合、重排 |
| 工作流 | `workflows/`、`jobs.py` | LangGraph 恢复、模型任务导出和实际结果导入 |
| 数据与实验 | `benchmark/`、`experiments/` | 角色隔离、反事实冻结、六条件、理解与短互动 |
| 评测与界面 | `evaluation/`、`web/` | 引用校验、访问受限单列、场景聚类、双人锁定评审 |

## 安装与运行

使用 Python 3.11、uv 和锁定依赖。本机 PostgreSQL 17 与 pgvector 安装见[基础设施说明](docs/infrastructure.md)。

```powershell
uv sync --extra dev --frozen
uv run python scripts/setup_database.py
uv run tracemind db init
uv run tracemind doctor
uv run pytest
```

数据库连接由忽略提交的 `.env` 中 `TRACEMIND_DSN` 提供，默认只监听 `127.0.0.1:55432`。BGE-M3与重排权重固定到具体 revision，保存在 `.local/` 并顺序加载；CUDA不可用或显存不足时使用同一模型的CPU推理。

模型任务需要**活跃 Codex 会话调度指定子Agent**。Python不会自行启动当前对话的内部工具，也不自动调用外部生成模型API。实际任务、输入、结果及来源哈希保存在本机，可断点恢复。

```powershell
uv run tracemind jobs export .local/outbox
# 指定子Agent实际生成结果并记录来源后：
uv run tracemind jobs import .local/result.json
uv run tracemind benchmark advance .local/datasets/v1
uv run tracemind benchmark audit .local/datasets/v1
```

研究查看器与人工盲评入口分开运行，使用不同访问凭据：

```powershell
# 设置随机 TRACEMIND_EXPLORER_SECRET（至少32字符）后：
uv run tracemind explorer serve
# PACKETS 必须来自实际候选回应，不能用测试夹具代填：
uv run tracemind review init .local/reviews.sqlite3 .local/packets.json reviewer1 reviewer2
# 设置随机 TRACEMIND_REVIEW_SECRET 后：
uv run tracemind review serve .local/reviews.sqlite3
```

[架构与任务恢复](docs/architecture.md) · [数据卡](docs/data_card.md)

新测试组设定、匿名映射和人工表位于忽略提交的 `.local/`。模型合成和评分存在共同偏好，不能称作人工或临床认可。

## 历史归档

本仓库保存一组**合成咨询对话**上的探索性实验，研究跨会话历史、按需重建和滚动维护的心理理解记录如何影响模型的理解与下一轮回应。数据不来自真实来访者；模型评分未经人工或临床校准，不能用于临床判断。

| 目录 | 内容 |
| --- | --- |
| [`pilot_three_cases/`](pilot_three_cases/) | 三例早期纵向实验及生成、审计脚本 |
| [`p0_understanding_in_action/`](p0_understanding_in_action/) | 8 例、64 次会话、24 个检查点的现象实验 |
| [`p1_evaluation/`](p1_evaluation/) | DeepSeek Pro 作答、Qwen Flash 评审的 P1 分析 |
| [`p1_1_qwen_rerun/`](p1_1_qwen_rerun/) | Qwen Flash 作答、Qwen Max 评审的独立复测 |

建议先阅读各目录的 `README.md` 和报告，再查看保存的答卷、评分与审计记录。两个 P1 版本都**没有证明**滚动维护优于从完整历史按需重建；新旧版本同时更换了作答和评审模型，不能将分数变化归因于单一因素。

脚本通过运行环境中的 `DASHSCOPE_API_KEY` 读取密钥。请勿将密钥、真实来访者资料或其他私人数据提交到仓库。
