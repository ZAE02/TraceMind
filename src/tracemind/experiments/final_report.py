"""Render audited aggregate results; never infer missing results or human judgments."""

from .runner import METHODS


def number(value):
    if value is None:
        return "未测得"
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def table(headers, rows):
    def cell(value):
        return number(value).replace("|", "\\|").replace("\n", " ")

    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
        + ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    )


def render_final_report(results, delivery, *, protocol_sha256, human_statistics):
    """Only callable after the full delivery audit and human-store checks succeed."""
    if not delivery.get("complete") or delivery.get("issues"):
        raise ValueError("final report requires a successful full delivery audit")
    if delivery["dataset"] != results["dataset"]:
        raise ValueError("report and audited dataset differ")
    if delivery["evaluation_checkpoint_counts"] != {
        "development": 12, "calibration": 20, "test": 60
    }:
        raise ValueError("incomplete evaluation coverage")
    if set(results["task_a"]) != set(METHODS) or any(
        item["checkpoints"] != 60 for item in results["task_a"].values()
    ):
        raise ValueError("incomplete Task A results")
    if human_statistics["paired_packets"] != 200 or human_statistics["assigned_judgments"] != 400:
        raise ValueError("incomplete human review package")
    blocks = [
        "# TraceMind 完整计算实验分析报告",
        "本报告由通过完整范围审计的实际结果生成。计算交付与人工判断分别验收；"
        "模型评分不是人工或临床认可。结果数据见 [experiment_results.json](experiment_results.json)。",
        "## 交付范围与证据",
        table(["项目", "实际数量", "要求数量"], [
            (label, results["dataset"][key], target)
            for label, key, target in (
                ("基础场景", "approved_plans", 20), ("完整轨迹", "complete_trajectories", 80),
                ("完整会话", "complete_sessions", 480), ("检查点", "checkpoints", 240)
            )
        ]),
        "基础场景按开发/校准/测试分为10/5/5组；同一场景的四条轨迹不视为独立样本。"
        "开发12个检查点用于流程验证，校准20个检查点用于独立重复评分，正式测试60个检查点。"
        "240个检查点均要求公开证据标注与独立复核。",
        f"冻结协议 SHA-256：`{protocol_sha256}`。任务结果和输入来源经过哈希核对；"
        "私有设定、测试原文、匿名映射和凭据保留在本地忽略目录。",
        table(["审计材料（含开发与校准）", "实际数量"],
              sorted(delivery["artifact_counts"].items())),
        "## 方法与比较口径",
        "六条件为 current（当前片段）、temporalrag（时序检索）、summary（摘要）、"
        "on_demand（按需重建）、evolution（持续修订）及诊断 oracle。"
        "Oracle仅获得批准的历史证据，不代表可部署的基线。所有条件使用同一Gemini底座；"
        "BGE-M3、重排模型和pgvector承担真实检索。历史输入上限4096 token。"
        "质量评审只见匿名答卷与公开历史，访问归因由另一次请求完成。",
    ]
    revision = results.get("cohort_revision")
    if revision:
        blocks += ["## 数据补全与选择偏差", (
            f"当前数据版本为 `{revision['version']}`，涉及{revision['replaced_trajectories']}条轨迹、"
            f"{revision['replacement_session_scope']}场补写范围。原始拒绝及原始材料保留，"
            "补写内容重新审查。补全发生于冻结前；本报告描述修订后队列，不能当作原始"
            "全部生成样本的无条件表现。筛选与补写可能引入选择偏差，后续应以独立新队列复验。"
        )]
    blocks += ["## Task A：理解质量", table(
        ["方法", "检查点", "证据利用均值", "理解更新均值"],
        [(m, results["task_a"][m]["checkpoints"],
          results["task_a"][m]["evidence_utilization_mean"],
          results["task_a"][m]["understanding_update_mean"]) for m in METHODS]
    ), "这些均值为模型评分的描述性统计，不把60个相关检查点当作60个独立场景。",
        "## Task B：回应质量与错误分析",
        "每个对照分别分析，不合并同一evolution答卷在不同对照中的重复评分。"
        "差值统一为表内左方法减右方法：错误率差值小于0有利于左方法，质量分差值大于0"
        "有利于左方法。区间按基础场景聚类自助抽样；仅五个独立测试场景，区间并非强确证。",
    ]
    for contrast in results["model_evaluation"]["contrasts"].values():
        blocks.append(f"### {contrast['left']} 对 {contrast['right']}")
        rows = []
        for method, summary in contrast["by_method"].items():
            for error, count in summary["counts"].items():
                rows.append((method, error, count, summary["denominators"][error],
                             summary["rates"][error]))
        blocks.append(table(["方法", "错误类型", "错误数", "可评估分母", "错误率"], rows))
        blocks.append("各错误分别剔除无法访问必要证据的样本；不同错误的分母可以不同。")
        rows = []
        for metric, delta in contrast["differences"].items():
            interval = delta["ci95"]
            ci = "未测得" if interval is None else f"[{number(interval[0])}, {number(interval[1])}]"
            assessment = "无法判断"
            if interval is not None:
                assessment = "区间包含0，方向尚不确定" if interval[0] <= 0 <= interval[1] else (
                    "区间为正" if interval[0] > 0 else "区间为负"
                )
            rows.append((metric, delta["matched_checkpoints"], delta["base_groups"],
                         delta["difference"], ci, assessment))
        blocks.append(table(["指标", "配对检查点", "独立场景", "左−右", "95%区间", "解释"], rows))
    clarification = results["clarification"]
    blocks += ["## 两轮互动：预定线索澄清与模拟器异常", table(
        ["方法", "互动数", "异常剔除", "可评估互动", "至少一项目标达成", "互动成功率", "目标数", "达成目标数"],
        [(m, v["interactions"], v["simulator_inconsistency_flagged"], v["eligible_interactions"],
          v["interactions_clarifying_any_predefined_target"], v["interaction_success_rate"],
          v["predefined_targets"], v["accurately_clarified_targets"])
         for m, v in clarification["methods"].items()]
    ), "预定目标在答题前冻结。模拟器异常样本单列并从成功率分母排除；"
       "因此成功率是剔除后的条件比例，不能解释成真实咨询疗效。", table(
           ["对temporalrag的比较", "共同检查点", "独立场景", "成功率差值", "95%区间"],
           [(m, v["matched_checkpoints"], v["base_groups"], v["difference"], v["ci95"])
            for m, v in clarification["paired_differences_vs_temporalrag"].items()]
       )]
    repeat = results["repeated_calibration"]
    blocks += ["## 校准重复评分的一致性",
               f"重复评分覆盖{repeat['calibration_checkpoints']}个校准检查点，评审来自同一模型的独立请求。",
               table(["评分维度", "平均绝对差"], sorted(repeat["mean_absolute_score_difference"].items())),
               table(["错误标签", "判断对数", "观察一致率", "Kappa"],
                     [(k, v["denominator"], v["observed"], v["kappa"])
                      for k, v in repeat["error_label_agreement"].items()]),
               "Kappa无法定义时保留缺失，不置为1。这里不声称真人间一致性。",
               "## 调用、预算与测得的资源消耗"]
    costs = results["method_costs"]
    blocks.append("下表只计正式测试答题及去重后的准备/更新任务，排除数据合成和评审；"
                  "耗时为请求耗时累计，不是并发运行的墙钟时间，也不是完整线上响应延迟。")
    blocks.append(table(["方法", "最大回应历史token", "答题任务数", "更新任务数", "答题耗时合计秒", "缺失耗时数"], [
        (m, v["maximum_answer_history_tokens"], v["answer_tasks"]["calls"],
         v["unique_preparation_update_tasks"]["calls"], v["answer_tasks"]["duration_seconds_sum"],
         v["answer_tasks"]["duration_missing"]) for m, v in costs["methods"].items()
    ]))
    telemetry = results["telemetry"]
    blocks.append(table(["全项目遥测", "数值"], [
        (key, telemetry[key]) for key in (
            "http_attempts", "http_200", "attempts_with_duration", "attempts_missing_duration",
            "mean_http_attempt_duration_seconds", "responses_with_usage", "retrievals_with_timing",
            "retrievals_missing_timing", "mean_initial_retrieval_seconds"
        )
    ]))
    blocks.append(table(["服务商返回的用量字段", "累计值"],
                        sorted(telemetry["provider_reported_usage_totals"].items())))
    blocks += ["全项目遥测包括早期诊断、被拒结果与重试；不能拿总调用量代替正式样本量。"
               "用量只汇总服务商实际返回的字段。无价格或账单依据，不推算费用；缺失计时不填零。",
               "## 人工盲评与使用边界",
               f"已构建{human_statistics['paired_packets']}组匿名配对，"
               f"分配{human_statistics['assigned_judgments']}份独立判断，"
               f"当前实际锁定提交{human_statistics['locked_judgments']}份。"
               "人工结果另行统计；未填写记录不纳入任何人工认可或效果结论。",
               "## 结论与后续验证",
               "结果只能支持当前合成队列、固定模型和固定预算下的探索性比较。"
               "同模型生成与评分可能产生偏好；四轨迹与多检查点存在组内相关；"
               "五个独立测试场景不足以支持广泛推广。应同时阅读各对照的差值、区间、错误分母、"
               "模拟器异常率与资源消耗，不选取单一有利指标宣称整体提升。",
               "后续依次完成真人匿名盲评、核查分歧与错误证据、独立新场景复验及异模型评审敏感性分析。"
               "这些后续研究不回写本轮冻结答卷，也不代替本轮计算交付。"]
    return "\n\n".join(blocks) + "\n"
