"""Resume the authorized project through real computational deliverables."""

import argparse
import os
import subprocess

from tracemind.api_batch import run_batch
from tracemind.api_client import APIStopped, LimitedAPI
from tracemind.benchmark.builder import write
from tracemind.config import LOCAL, ROOT, dsn
from tracemind.experiments.project import ProjectPipeline
from tracemind.jobs import JobQueue

parser = argparse.ArgumentParser()
parser.add_argument("--batch-id", required=True)
args = parser.parse_args()
root = LOCAL / "datasets/gemini_v1"
pipeline = ProjectPipeline(root)
try:
    synthesis = run_batch(args.batch_id, progress_callback=pipeline.progress)
    if synthesis["status"] != "completed":
        pipeline.status("stopped", reason=synthesis.get("reason", synthesis["status"]))
        raise SystemExit(1)
    api = LimitedAPI()
    queue = JobQueue(dsn(), model=api.model, reasoning="provider-default")
    results = pipeline.finish(queue, api)
    pipeline.status("validating_release")
    from tracemind.experiments.completion import audit_delivery

    delivery = audit_delivery(root, queue)
    write(LOCAL / "project/completion_audit.json", delivery)
    if not delivery["complete"]:
        raise APIStopped("full-scope computational delivery audit failed; release not published")
    env = {**os.environ, "TRACEMIND_TEST_POSTGRES_DSN": dsn(), "TRACEMIND_TEST_DSN": dsn()}
    import sys

    with (LOCAL / "project/validation.log").open("w", encoding="utf-8") as log:
        checked = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"], cwd=ROOT, env=env, stdout=log, stderr=log
        )
    if checked.returncode:
        raise APIStopped("final regression checks failed; release not published")
    from tracemind.benchmark.export import export_development

    export_development(root, ROOT / "data/examples/gemini_v1", 3, queue)
    pipeline.status("computational_deliverables_validated_human_review_pending")
    subprocess.run(
        [sys.executable, "scripts/report_status.py"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    text = (
        "# TraceMind 计算实验交付\n\n"
        "20 组基础场景、80 条轨迹、480 场会话、240 个检查点已通过数量与来源核查。\n\n"
        "测试集包含五个独立基础场景、60 个检查点；五个正式方法与一个诊断 Oracle 使用相同底座及历史预算。"
        "结果汇总见 experiment_results.json。模型评审含同模型偏好，统计按基础场景聚类，仅作合成小样本探索。\n\n"
        "200 组匿名配对及400个独立人工判断的工具已创建，人工判断未代填。人工完成前不宣称临床或人工认可。\n"
    )
    (ROOT / "docs/final_report.md").write_text(text, encoding="utf-8")
    readme = ROOT / "README.md"
    import re

    updated, count = re.subn(
        r"\*\*v0\.2 状态：\*\*[^\n]*",
        "**v0.2 状态：**20组、80条轨迹、480场会话、240个检查点的计算实验已完成；实际模型评测见 docs/experiment_results.json，200组人工配对和400份独立判断工具已提供，人工判断仍待完成。不以模型评分宣称人工或临床认可。",
        readme.read_text(encoding="utf-8"),
        count=1,
    )
    if count != 1:
        raise APIStopped("README release status marker changed; preserve results for review")
    readme.write_text(updated, encoding="utf-8")
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    remote = subprocess.check_output(
        ["git", "remote", "get-url", "origin"], cwd=ROOT, text=True
    ).strip()
    if (
        branch != "feat/tracemind-v02"
        or remote.rstrip("/").removesuffix(".git") != "https://github.com/ZAE02/TraceMind"
    ):
        raise APIStopped("release branch or remote changed; preserved results without committing")
    files = [
        "README.md",
        "docs/status.json",
        "docs/experiment_results.json",
        "docs/final_report.md",
        "data/examples/gemini_v1",
    ]
    from tracemind.api_client import LimitedAPI

    key = LimitedAPI().key
    for item in files:
        target = ROOT / item
        paths = [target] if target.is_file() else list(target.rglob("*"))
        if any(path.is_file() and key.encode() in path.read_bytes() for path in paths):
            raise APIStopped("credential detected in release files; publishing blocked")
    subprocess.run(["git", "add", *files], cwd=ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet", "--", *files], cwd=ROOT).returncode:
        subprocess.run(
            [
                "git",
                "commit",
                "--only",
                "-m",
                "Publish measured TraceMind computational results; human review pending",
                "--",
                *files,
            ],
            cwd=ROOT,
            check=True,
        )
    subprocess.run(["git", "push", "origin", branch], cwd=ROOT, check=True)
    local = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    remote_head = subprocess.check_output(
        ["git", "ls-remote", "origin", "refs/heads/" + branch], cwd=ROOT, text=True
    ).split()[0]
    if local != remote_head:
        raise APIStopped("remote revision differs after release push")
    write(
        LOCAL / "project/release.json",
        {"revision": local, "computational_deliverables": "complete", "human_review": "pending"},
    )
    pipeline.status("computational_deliverables_complete_human_review_pending", revision=local)
except Exception as exc:
    if not isinstance(exc, APIStopped):
        from tracemind.diagnostics import record_failure

        record_failure(LOCAL / "project/local_failure.json", exc)
    pipeline.status(
        "stopped",
        reason=str(exc) if isinstance(exc, APIStopped) else type(exc).__name__,
        detail="Inspect preserved source jobs and raw outputs; no automatic content retries or model switching",
    )
    raise SystemExit(1) from None
