# TraceMind 项目指令

调用本项目任何生成模型 API 前，必须每次读取
[tracemind-api skill](.agents/skills/tracemind-api/SKILL.md)，并通过其持久化限流器执行。
凭据只读环境变量或忽略提交的本地配置，不得提交或打印。

数据合成改用用户指定的 `gemini-3.8-flash`，停止使用 GPT-6.1 Terra 子 Agent 合成。
用户已取消每批 20 条及每日请求数量上限，允许持续推进已授权项目目标。
全局并发仍最多 1，请求开始间隔至少 30 秒；原错误处理及最多两次重试规则保留。
已有 Terra 数据保留来源，Gemini 数据使用独立 namespace，不混写旧结果。
