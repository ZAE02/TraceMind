# 三组纵向心理咨询案例

> 全部为拟真合成材料；非真实患者记录。按用户授权，由模型子 Agent 审阅；前期 Sol/high、后续 Luna/low。人工评分未填写。

已完成 C01、C02、C03 各8次会话，共24次，并从实际对话冻结9个评测节点。
请从 [完整案例与审阅入口](audit/review_packet.md) 开始阅读。实际轮数、评分和核验结果见该入口与 `audit/final_validation.json`。

## 内容与权限

- `dialogues/`：逐条生成的JSON原始发言和Markdown阅读版。每条均有API调用引用。
- `configs/case_plans/`：3份人物与24张会话卡；`research_plans/`：研究者心理发展计划。
- `facts/`：附证据编号的新事实账本和阅读版。
- `generation_logs/full_agent_calls.jsonl`：实际请求、响应、模型标识、参数、时间与重试；不保存认证头。
- `audit/agent_reviews/`：逐会话主审及各案例总结；实际模型与推理强度见每份审阅。
- `audit/human_review_forms/`：24份两位人工审阅者的空白表；`quality_scores.json`均为null。
- `checkpoints/public/`：待测模型唯一可见的9份冻结输入。
- `checkpoints/C0N.json`：证据、未知事项、探索目标及无依据预设；无唯一标准回复。
- `checkpoints/private/`：已发生经历与当前私人体验，仅供后续互动模拟，不代表客观心理真值。
- `audit/smoke_answers/qwen3.8-27b/`：9个节点的公开输入试答；其他未完成的可选GPT试答保留但不计入正式结果，见 `audit/optional_review_deviation.json`。`revisions/`：原稿、失败稿和返修记录。

两个角色每次只生成一条发言，下一角色接收真实输出。咨询师只看实际公开历史和中性会话信息；来访者仅看自身已发生经历与当前体验。研究轨迹、标注和未来事件不进入角色请求。

## 运行

Python3.9以上，仅标准库。将密钥注入当前生成进程的 `DASHSCOPE_API_KEY`，勿写入文件。

```powershell
python .\generator.py --phase preflight
python .\run_longitudinal.py
python .\export_checkpoints.py
python .\smoke_checkpoints.py
python .\generator.py --phase check
python .\final_audit.py
python .\finalize_documents.py
python -m unittest discover -s tests -q
```

`run_longitudinal.py`按案例并行、同一案例按会话顺序运行。它通过 `generation_logs/review_ready/` 向外部审阅Agent提交冻结哈希，并等待对应 `audit/agent_reviews/C0N/SNN.json`；Python自身不调用GPT子Agent。没有合格审阅文件时会等待，不自动代填审阅。既有完成会话跳过，成功发言和调用日志支持断点恢复。

人工门槛原样保留在生成器中；本次 `configs/review_policy.json` 记录用户明确授权的模型审阅替代方案。前期模型均分≥4；用户后续降低限制，后续 Luna/low 均分≥3且无重大问题即可续作，取消强制双模型复审。模型审阅不能被称为人工通过。

非流式Responses请求；Qwen使用temperature0.7、来访者 reasoning low；v8咨询师 medium，v9及早期为 low；试答 low；max_output_tokens4096、store=false，不使用previous_response_id。网络/429/服务端暂时错误最多重试2次，鉴权和格式错误停止。超时后服务端是否处理无法确认，重试可能额外计费。

## 变更与证据限制

原业务空间入口返回403；同一凭据在阿里云北京通用入口成功。全部案例由 `qwen3.8-27b` 生成，原入口失败记录保留。DeepSeek备用模型在通用入口返回MODEL_NOT_FOUND，不能记为成功生成或成功试答。

为解决重复引用、微观词句盘问与冗长告别，逐次修订通用表达规则，后续启用尚未发送的咨询师候选检查与口语编辑，最多2次重新生成；原候选与实际采用的发言均在调用日志中。来访者仅收到最终保存的实际发言，未手工改写文本。前半段提供中性会话时间提示，避免提前告别后凑轮数。旧版本与所有失败稿保存在 `configs/revisions/`、`revisions/`，实际版本以逐条调用日志为准。修订没有提供个案心理答案，也没有诱导咨询师故意沿用旧解释。

用户后续要求节省时间与token，v9将剩余角色推理降至low，并关闭措辞候选编辑门槛；已保存发言原样保留，后续直接保存实际模型输出。重大问题仍由一次轻量审阅检查。不同版本与门槛并存，逐条调用及逐会话审阅记录为准。

这三组材料用于验证任务可行性和评测节点设计。筛选、返修和模型标注存在偏差，尚未经过人工或临床验证。有限试答不能证明RAG存在某种错误，也不能证明显式心理轨迹更优。以上是案例生成阶段的结果；后续用户授权的P2历史消融、方法对照及短互动实验见 [P2说明](p2_validation/README.md) 和 [验证报告](p2_validation/pilot_validation_report.md)。未开展训练。

接口依据：[阿里云Responses文档](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses)、[地域与入口](https://help.aliyun.com/zh/model-studio/regions)、[DeepSeek接口文档](https://help.aliyun.com/zh/model-studio/deepseek-api)。原始计划保存在 `docs/source_generation_plan.md`；项目研究想法在上级目录。

