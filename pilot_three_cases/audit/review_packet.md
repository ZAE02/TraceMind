# 完整案例与评测节点

> 拟真合成材料，非真实患者记录。前期 GPT-6.1 Sol/high、后续 GPT-6 Luna/low 单次模型审阅；未进行人工审阅。

生成器逐条调用 Qwen，来访者和咨询师独立接收权限限定的上下文。原始输出、失败稿、返修稿和完整调用日志均保留。

| 案例 | 会话 | 往返数 | 完整对话 | 主审三项评分 | 人工表 |
| --- | --- | --- | --- | --- | --- |
| C01 | S01 | 12 | [对话](../dialogues/C01/S01.md) | 4/4/4 | [空白表](human_review_forms/C01_S01.md) |
| C01 | S02 | 10 | [对话](../dialogues/C01/S02.md) | 4/4/4 | [空白表](human_review_forms/C01_S02.md) |
| C01 | S03 | 10 | [对话](../dialogues/C01/S03.md) | 4/4/4 | [空白表](human_review_forms/C01_S03.md) |
| C01 | S04 | 9 | [对话](../dialogues/C01/S04.md) | 4/4/4 | [空白表](human_review_forms/C01_S04.md) |
| C01 | S05 | 10 | [对话](../dialogues/C01/S05.md) | 4/4/4 | [空白表](human_review_forms/C01_S05.md) |
| C01 | S06 | 8 | [对话](../dialogues/C01/S06.md) | 4/4/4 | [空白表](human_review_forms/C01_S06.md) |
| C01 | S07 | 11 | [对话](../dialogues/C01/S07.md) | 4/4/4 | [空白表](human_review_forms/C01_S07.md) |
| C01 | S08 | 8 | [对话](../dialogues/C01/S08.md) | 4/3/4 | [空白表](human_review_forms/C01_S08.md) |
| C02 | S01 | 10 | [对话](../dialogues/C02/S01.md) | 4/4/4 | [空白表](human_review_forms/C02_S01.md) |
| C02 | S02 | 10 | [对话](../dialogues/C02/S02.md) | 4/4/4 | [空白表](human_review_forms/C02_S02.md) |
| C02 | S03 | 10 | [对话](../dialogues/C02/S03.md) | 4/4/4 | [空白表](human_review_forms/C02_S03.md) |
| C02 | S04 | 10 | [对话](../dialogues/C02/S04.md) | 4/4/4 | [空白表](human_review_forms/C02_S04.md) |
| C02 | S05 | 10 | [对话](../dialogues/C02/S05.md) | 4/3/4 | [空白表](human_review_forms/C02_S05.md) |
| C02 | S06 | 10 | [对话](../dialogues/C02/S06.md) | 4/3/4 | [空白表](human_review_forms/C02_S06.md) |
| C02 | S07 | 10 | [对话](../dialogues/C02/S07.md) | 4/3/4 | [空白表](human_review_forms/C02_S07.md) |
| C02 | S08 | 10 | [对话](../dialogues/C02/S08.md) | 4/3/4 | [空白表](human_review_forms/C02_S08.md) |
| C03 | S01 | 10 | [对话](../dialogues/C03/S01.md) | 4/4/4 | [空白表](human_review_forms/C03_S01.md) |
| C03 | S02 | 10 | [对话](../dialogues/C03/S02.md) | 4/4/4 | [空白表](human_review_forms/C03_S02.md) |
| C03 | S03 | 10 | [对话](../dialogues/C03/S03.md) | 4/4/4 | [空白表](human_review_forms/C03_S03.md) |
| C03 | S04 | 9 | [对话](../dialogues/C03/S04.md) | 4/4/4 | [空白表](human_review_forms/C03_S04.md) |
| C03 | S05 | 10 | [对话](../dialogues/C03/S05.md) | 4/4/4 | [空白表](human_review_forms/C03_S05.md) |
| C03 | S06 | 9 | [对话](../dialogues/C03/S06.md) | 4/4/4 | [空白表](human_review_forms/C03_S06.md) |
| C03 | S07 | 10 | [对话](../dialogues/C03/S07.md) | 4/4/4 | [空白表](human_review_forms/C03_S07.md) |
| C03 | S08 | 9 | [对话](../dialogues/C03/S08.md) | 4/4/4 | [空白表](human_review_forms/C03_S08.md) |

整条阅读版：[C01](../dialogues/C01_all.md)、[C02](../dialogues/C02_all.md)、[C03](../dialogues/C03_all.md)。

## 九个冻结节点

| 案例 | CP1：早期 | CP2：中期 | CP3：S8 |
| --- | --- | --- | --- |
| C01 | [CP1](../checkpoints/C01_CP1.md) | [CP2](../checkpoints/C01_CP2.md) | [CP3](../checkpoints/C01_CP3.md) |
| C02 | [CP1](../checkpoints/C02_CP1.md) | [CP2](../checkpoints/C02_CP2.md) | [CP3](../checkpoints/C02_CP3.md) |
| C03 | [CP1](../checkpoints/C03_CP1.md) | [CP2](../checkpoints/C03_CP2.md) | [CP3](../checkpoints/C03_CP3.md) |

公开试答输入在 `checkpoints/public/`，研究标注在 `checkpoints/C0N.json`，私人模拟资料在 `checkpoints/private/`。
试答程序只读公开输入；标注依据冻结时点以前的真实对话，不包含唯一标准回复。

## 审阅与限制

- [逐会话主审](agent_reviews/)记录评分、证据、未知事项及小问题。
- 用户要求节省时间和token后，取消强制第二次复审与重复GPT试答；未完成材料仅为草稿。
- [最终程序核验](final_validation.json)检查原始调用、完整前缀、24次会话与9个节点。
- [有限试答](smoke_run.json)用于检查输入、难度和预设风险，不能推断模型优劣或RAG效果。
- 人工评分全部为空。案例经过筛选与返修，不能用于估算模型在未经筛选咨询中的错误率。
