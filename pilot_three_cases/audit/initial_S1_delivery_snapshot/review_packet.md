# 三个 S1：人工审阅入口

> 拟真合成咨询材料，非真实患者记录。三个案例已生成；人工质量验收待完成。两位审阅者请先分别阅读完整对话，再评分。

| 案例 | 本次主题 | 完整对话 | 往返数 | 人工评分表 |
| --- | --- | --- | --- | --- |
| C01 · 林岚 | 模糊工作反馈后反复修改与确认 | [阅读C01](C:/Users/99291/Desktop/Mem/pilot_three_cases/dialogues/C01/S01.md) | 12 | [C01评分表](C:/Users/99291/Desktop/Mem/pilot_three_cases/audit/human_review_forms/C01_S01.md) |
| C02 · 陈宁 | 答应额外任务，挤占自己的安排 | [阅读C02](C:/Users/99291/Desktop/Mem/pilot_three_cases/dialogues/C02/S01.md) | 10 | [C02评分表](C:/Users/99291/Desktop/Mem/pilot_three_cases/audit/human_review_forms/C02_S01.md) |
| C03 · 许哲 | 技术排查停滞后的烦躁与身体感受 | [阅读C03](C:/Users/99291/Desktop/Mem/pilot_three_cases/dialogues/C03/S01.md) | 12 | [C03评分表](C:/Users/99291/Desktop/Mem/pilot_three_cases/audit/human_review_forms/C03_S01.md) |

每条发言有编号，可直接引用编号提出修改意见。共34组往返、68条实际生成发言，每条都能对应到一次独立角色请求。生成模型为 `qwen3.8-27b`，双角色实际逐轮交互。

## 审阅方法

两位不同的人工审阅者各自评价自然性、咨询质量、纵向合理性，使用1–5分。S1的纵向合理性仅评价起点可信度与可延展性，完整轨迹待后续会话检验。六项平均不低于4分，并且两人均确认无重大事实冲突、Gold泄漏或严重不当咨询行为，才能通过人工门槛。

评分与意见可填写在上述表中，或直接提供“案例编号、审阅者编号、三项评分、问题发言编号及理由”。模型预审不计入人工分数。[机器可读人工评分文件](C:/Users/99291/Desktop/Mem/pilot_three_cases/audit/quality_scores.json)中的分数仍为 `null`。

## 模型预审提示

- 共性：咨询师反复引用词语、赞许其“准确或重要”，有模板感；部分解释偏强，需检查是否容许来访者修正。
- C01：U19–U20中“方案没有实际问题”未获客观核实。U21的“一遍”仅是打算，不代表行为已经改变。
- C02：U09将晚餐情境放在上周六，会话卡安排的是周四取消晚餐。可能是另一次事件，需核对；原文与日期未作改写。该事实连贯性检查保持待确认。
- C03：本次尚未谈到是否求助或能力评价顾虑，不能把计划中的心理解释当作当前对话证据。该S1的研究信息量需要人审判断。

[完整模型预审](C:/Users/99291/Desktop/Mem/pilot_three_cases/audit/model_pre_review.json)已列出支持证据、未确定事项与不应推出的结论。这些是审阅提示，不是Benchmark Gold。

## 记录与下一步

原业务空间域名对Qwen和DeepSeek都返回403；同一密钥通过阿里云官方北京通用入口测试成功，随后全部咨询发言由Qwen生成。DeepSeek没有生成咨询数据。地址调整与失败记录已保留，密钥未写入项目。

[运行与质量记录](C:/Users/99291/Desktop/Mem/pilot_three_cases/audit/review_notes.md)包含验证结果和接入修订。生成已停在人工审阅阶段；S2–S8及约9个checkpoint尚未生成。收到人工反馈后先处理问题、保留原始版本与修订记录，再继续后续会话。

