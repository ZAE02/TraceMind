# P1.1：Qwen Flash 作答、Qwen Max 评分复测

本目录独立保存复测结果。`source_p0/` 复制原 P0 的 8 个合成案例、24 个检查点、96 份四条件输入与检索结果；其 192 份 Task A/B 答卷由 `qwen3.7-flash-2026-07-15` 重新生成。`frozen_p0/` 是进入评分流程的冻结副本。`qwen3.8-max` 重新复核参考标注并盲评。原 `p0_understanding_in_action/` 和 `p1_evaluation/` 均未覆盖。

执行流程与原 P1 一致，包括四条件、102 份按本次评分规则选出的复评、C4/C5、C5-minus、直接回应、预算对照和 C4-full 同完整历史对照。主结果见 [P1.1复测报告](P1.1复测报告.md)、`analysis/summary.json`、`analysis/mechanism_summary.json`；逐答卷评分见 `analysis/*.csv`。`audit.json` 与 `verification.json` 分别记录流程完整性、历史边界、模型身份和与原 P0 输入的哈希一致性。

脚本使用 Python 标准库。API 密钥只从运行进程的 `DASHSCOPE_API_KEY` 或 `run_all.py` 的 stdin 读取，不保存在目录中。`run_all.py` 逐阶段续跑，已保存且有效的结果会跳过；如需重新生成某项评分，先将其保存到 `quarantined_nonverbatim/` 之类的审计目录再续跑。`setup_run.py` 仅用于首次准备，当前目录已准备完成。

盲评映射位于 `internal/`、`mechanisms/`、`budget_match/` 和 `full_access/` 下的 `*blind_key.json`，交给独立评审者时应排除这些文件。材料全为合成咨询对话，未进行人工或临床评审。Qwen Flash 和 Qwen Max 同属 Qwen 系列；本复测不能替代独立模型家族或人工校准。
