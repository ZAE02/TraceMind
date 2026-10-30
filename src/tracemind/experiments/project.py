"""Resumable project phases: smoke, calibration, test, review packets, report."""

import json
import secrets
from pathlib import Path

from tracemind.api_client import APIStopped
from tracemind.benchmark.builder import read, write
from tracemind.config import LOCAL, ROOT, dsn
from tracemind.jobs import digest

from .api_executor import TrackingQueue, execute_job
from .targets import current_card, prepare_targets


class ProjectPipeline:
    def __init__(self, root):
        self.root = Path(root)
        self.status_path = LOCAL / "project/status.json"
        self.runner = None

    def status(self, phase, **kwargs):
        from datetime import datetime, timezone

        write(
            self.status_path,
            {
                "phase": phase,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "human_review": "pending_not_model_filled",
                **kwargs,
            },
        )

    def setup(self, queue):
        if self.runner is None:
            from tracemind.experiments.runner import ExperimentRunner
            from tracemind.memory import PostgresMemoryStore
            from tracemind.retrieval.models import BGEModels
            from tracemind.retrieval.pipeline import Retriever
            from tracemind.storage.repository import EvidenceRepository

            models = BGEModels()
            self.tracking = TrackingQueue(queue)
            repo = EvidenceRepository(dsn())
            self.runner = ExperimentRunner(
                self.root,
                self.tracking,
                repo,
                Retriever(repo, models),
                models,
                PostgresMemoryStore(dsn()),
            )
            manifest = {
                "answer_model": queue.model,
                "judge_model": queue.model,
                "reasoning": queue.reasoning,
                "execution": "stateless_api_jobs",
                "same_model_bias": True,
                "legacy_results_reused": False,
                "protocol": "quality-v1",
                "method_versions": {
                    "on_demand": "hybrid-evidence-rebuild-v2",
                    "evolution": "checkpoint-bound-append-only-v1",
                },
            }
            path = self.root / "experiment_manifest.json"
            if path.exists() and read(path) != manifest:
                old = read(path)
                compatible = {
                    key: value for key, value in manifest.items() if key != "method_versions"
                }
                formal_started = any(
                    (self.root / "experiments/candidates" / cp["id"]).exists()
                    for cp in self.checkpoints()
                    if cp["split"] in ("calibration", "test")
                )
                if (
                    old == compatible
                    and not formal_started
                    and not (self.root / "protocol/frozen.json").exists()
                ):
                    write(self.root / "project/development_legacy_manifest.json", old)
                    write(path, manifest)
                else:
                    raise APIStopped("experimental model configuration changed; create a new run")
            if not path.exists():
                write(path, manifest)

    def checkpoints(self, split=None, group=None):
        return [
            c
            for p in sorted((self.root / "checkpoints").glob("*.json"))
            if (c := read(p))
            and (split is None or c["split"] == split)
            and (group is None or c["group_id"] == group)
        ]

    def group_complete(self, group):
        for branch in range(1, 5):
            for session in range(1, 7):
                case = f"gemini_v1_{group}-T{branch}"
                p = self.root / "dialogues" / case / f"S{session:02}.json"
                r = self.root / "dialogue_reviews" / f"{case}_S{session:02}.json"
                if (
                    not p.exists()
                    or not r.exists()
                    or not read(p).get("complete")
                    or not read(r).get("approved")
                ):
                    return False
        return True

    def drive_checkpoint(self, queue, api, checkpoint, phase):
        self.setup(queue)
        prepare_targets(self.root, queue, api, checkpoint)
        while True:
            if (LOCAL / "api/STOP").exists():
                raise APIStopped("project paused by STOP marker")
            self.status(phase, checkpoint_id=checkpoint["id"])
            self.tracking.requested.clear()
            result = self.runner.advance({checkpoint["id"]})
            pending = [
                ident
                for ident in self.tracking.requested
                if queue.get(ident)["status"] == "pending"
            ]
            if pending:
                execute_job(queue, api, pending[0], self.root / "experiments/api_raw")
                continue
            if result["waiting"]:
                raise APIStopped(
                    "experiment waiting without an executable task; inspect calibration/targets"
                )
            break
        self.simulator_check(queue, api, checkpoint)
        from .runner import METHODS

        required = []
        for method in METHODS:
            required += [
                self.root / "experiments" / kind / checkpoint["id"] / (method + ".json")
                for kind in ("candidates", "understanding", "understanding_quality")
            ]
            if method != "evolution":
                required.append(
                    self.root / "experiments/quality" / checkpoint["id"] / (method + ".json")
                )
                required += [
                    self.root
                    / "experiments/access"
                    / checkpoint["id"]
                    / (method + "--" + side + ".json")
                    for side in (method, "evolution")
                ]
        if any(not path.exists() for path in required):
            raise APIStopped("checkpoint completion missing required answer or review artifacts")
        write(
            self.root / "experiments/completed" / (checkpoint["id"] + ".json"),
            {
                "checkpoint_sha256": checkpoint["public_sha256"],
                "model": queue.model,
                "phase": phase,
            },
        )

    def annotate_checkpoint(self, queue, api, checkpoint):
        """Annotate every benchmark checkpoint, including unused development ones."""
        from .annotations import advance_annotation

        self.setup(queue)
        while True:
            if (LOCAL / "api/STOP").exists():
                raise APIStopped("project paused by STOP marker")
            self.status("benchmark_annotation", checkpoint_id=checkpoint["id"])
            self.tracking.requested.clear()
            result = advance_annotation(self.root, self.tracking, checkpoint)
            if result is not None:
                return result
            pending = [
                ident
                for ident in self.tracking.requested
                if queue.get(ident)["status"] == "pending"
            ]
            if not pending:
                raise APIStopped("annotation waiting without an executable task")
            execute_job(queue, api, pending[0], self.root / "experiments/api_raw")

    def simulator_check(self, queue, api, cp):
        schema = {
            "type": "object",
            "required": ["unsupported_agreement", "quotes", "rationale"],
            "properties": {
                "unsupported_agreement": {"type": "boolean"},
                "quotes": {"type": "array", "items": {"type": "string"}},
                "rationale": {"type": "string"},
            },
            "additionalProperties": False,
        }
        for method in ("temporalrag", "on_demand", "evolution"):
            path = self.root / "experiments/interactions" / cp["id"] / (method + ".json")
            interaction = read(path)
            targets = read(self.root / "experiments/interaction_targets" / (cp["id"] + ".json"))
            ctx = {
                "transcript": interaction["transcript"],
                "private_current": targets["private_current"],
                "instruction": "审查模拟器是否仅为迎合咨询师而同意卡片未支持的因果或编造事实。合理披露当前设定不算迎合。若标true，quotes逐字引用来访者发言；没有证据应false且空数组。",
            }
            jid = queue.enqueue(
                f"simulator-check:{cp['id']}:{method}",
                "independent simulator consistency reviewer",
                ctx,
                schema,
            )
            job = execute_job(queue, api, jid, self.root / "experiments/api_raw")
            output = job["result"]
            if output["unsupported_agreement"] and not output["quotes"]:
                raise APIStopped("simulator inconsistency flag requires actual transcript quotes")
            if any(
                not quote
                or not any(
                    t["role"] == "client" and quote in t["text"] for t in interaction["transcript"]
                )
                for quote in output["quotes"]
            ):
                raise APIStopped("simulator review quote is not a client transcript substring")
            write(
                self.root / "experiments/simulator_checks" / cp["id"] / (method + ".json"),
                {"job_id": jid, "result_sha256": job["result_sha256"], "result": output},
            )

    def counterfactual_review(self, queue, api, group):
        schema = {
            "type": "object",
            "required": ["approved", "issues"],
            "properties": {
                "approved": {"type": "boolean"},
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        }
        for stage in (2, 4, 6):
            cps = [cp for cp in self.checkpoints(group=group) if cp["session"] == stage]
            if len(cps) != 4 or len({cp["latest_client"]["text"] for cp in cps}) != 1:
                raise APIStopped("counterfactual review requires four identical current openings")
            ctx = {
                "shared_current": cps[0]["latest_client"]["text"],
                "histories": [],
                "instruction": "审查同一开场能否在四条已有公开历史与当前卡片中自然成立；不要要求开场揭示不同私有心理状态。检查历史与当前是否直接矛盾。仅依据给出的历史锚点作有限模型审查，不能称为完整人工认可。严重矛盾拒绝并指出具体分支/发言。",
            }
            for cp in cps:
                _, card = current_card(self.root, cp)
                anchors = []
                for session in cp["previous_sessions"]:
                    clients = [u for u in session["utterances"] if u["role"] == "client"]
                    anchors.append({key: clients[-1][key] for key in ("id", "text")})
                ctx["histories"].append(
                    {"case_id": cp["case_id"], "anchors": anchors, "current_card": card}
                )
            jid = queue.enqueue(
                f"counterfactual-review:{group}:S{stage}",
                "counterfactual data reviewer",
                ctx,
                schema,
            )
            job = execute_job(queue, api, jid, self.root / "experiments/api_raw")
            write(
                self.root / "counterfactual_reviews" / f"{group}_S{stage}.json",
                {"job_id": jid, "result_sha256": job["result_sha256"], **job["result"]},
            )
            if not job["result"]["approved"]:
                raise APIStopped("counterfactual review rejected; inspect preserved issues")

    def progress(self, root, queue, api):
        # A real first-checkpoint test catches integration failures early.
        cps = self.checkpoints(group="G001")
        first = next(
            (cp for cp in cps if cp["case_id"].endswith("-T1") and cp["session"] == 2), None
        )
        initial = self.root / "project/initial_smoke.json"
        if first and not initial.exists():
            self.drive_checkpoint(queue, api, first, "initial_pipeline_smoke")
            write(initial, {"checkpoint_id": first["id"], "status": "completed_actual_six_methods"})
        # Exercise multiple revisions early, rather than only the single-session
        # history of CP1. These are planned development checkpoints, not extra tests.
        first_case_complete = all(
            (self.root / "dialogue_reviews" / f"gemini_v1_G001-T1_S{number:02}.json").exists()
            and read(self.root / "dialogue_reviews" / f"gemini_v1_G001-T1_S{number:02}.json").get(
                "approved"
            )
            for number in range(1, 7)
        )
        if first_case_complete:
            for cp in cps:
                if (
                    cp["case_id"].endswith("-T1")
                    and not (self.root / "experiments/completed" / (cp["id"] + ".json")).exists()
                ):
                    self.drive_checkpoint(queue, api, cp, "longitudinal_pipeline_smoke")
        group_done = self.root / "project/group_smoke.json"
        if self.group_complete("G001") and not group_done.exists():
            self.counterfactual_review(queue, api, "G001")
            for cp in self.checkpoints(group="G001"):
                if not (self.root / "experiments/completed" / (cp["id"] + ".json")).exists():
                    self.drive_checkpoint(queue, api, cp, "group_pipeline_smoke")
            write(
                group_done,
                {"group": "G001", "checkpoints": 12, "status": "completed_actual_six_methods"},
            )
        self.status("synthesis", model=api.model)

    def finish(self, queue, api):
        from tracemind.benchmark.builder import BenchmarkBuilder
        from tracemind.evaluation.export import export_packets
        from tracemind.evaluation.metrics import collect_metric_rows
        from tracemind.evaluation.paired import paired_report
        from tracemind.experiments.protocol import freeze_protocol
        from tracemind.web.store import ReviewStore

        data = BenchmarkBuilder(self.root, queue).audit()
        if not data["complete"]:
            raise APIStopped("full dataset counts or provenance validation incomplete")
        for group in sorted({cp["group_id"] for cp in self.checkpoints()}):
            self.counterfactual_review(queue, api, group)
        self.setup(queue)
        for cp in self.checkpoints():
            self.annotate_checkpoint(queue, api, cp)
        calibration = []
        for group in sorted({cp["group_id"] for cp in self.checkpoints("calibration")}):
            available = self.checkpoints("calibration", group)
            for branch, stage in ((1, 2), (2, 4), (3, 6), (4, 2)):
                calibration.append(
                    next(
                        cp
                        for cp in available
                        if cp["case_id"].endswith(f"-T{branch}") and cp["session"] == stage
                    )
                )
        refs = []
        from tracemind.evaluation.protocol import QUALITY_SCHEMA

        for cp in calibration:
            self.drive_checkpoint(queue, api, cp, "calibration")
            quality = read(self.root / "experiments/quality" / cp["id"] / "temporalrag.json")
            first = queue.get(quality["job_id"])
            repeat = queue.enqueue(
                "calibration-repeat:" + first["id"],
                "blinded quality reviewer",
                first["request"]["allowed_context"],
                QUALITY_SCHEMA,
            )
            execute_job(queue, api, repeat, self.root / "experiments/api_raw")
            refs.append(
                {"checkpoint_id": cp["id"], "quality_job_id": first["id"], "repeat_job_id": repeat}
            )
        freeze_protocol(self.root, queue, refs)
        for cp in self.checkpoints("test"):
            self.drive_checkpoint(queue, api, cp, "test_evaluation")
        results = {
            "models": read(self.root / "experiment_manifest.json"),
            "dataset": data,
            "model_evaluation": paired_report(collect_metric_rows(self.root, "test", queue)),
            "human_review": "pending; no invented judgments",
            "same_model_bias": True,
            "conclusion_scope": "five independent test base scenes; exploratory synthetic benchmark",
            "diagnostic_method": "oracle; not a deployable baseline",
        }
        from .reports import (
            clarification_report,
            method_cost_report,
            repeat_report,
            task_a_report,
            telemetry_report,
        )

        results.update(
            task_a=task_a_report(self.root, queue, self.checkpoints("test")),
            repeated_calibration=repeat_report(self.root, queue),
            clarification=clarification_report(self.root, queue, self.checkpoints("test")),
            telemetry=telemetry_report(self.root),
            method_costs=method_cost_report(self.root, queue, self.checkpoints("test")),
        )
        private = LOCAL / "project/packets.json"
        results["packets"] = export_packets(self.root, private, queue)
        database = LOCAL / "reviews.sqlite3"
        store = ReviewStore(database)
        with store.connect() as conn:
            initialized = bool(conn.execute("SELECT count(*) FROM reviewers").fetchone()[0])
        if not initialized:
            credentials = {
                "reviewer1": secrets.token_urlsafe(24),
                "reviewer2": secrets.token_urlsafe(24),
            }
            store.initialize(credentials, read(private))
            write(LOCAL / "review_credentials.json", credentials)
        with store.connect() as conn:
            actual = {
                ident: digest(json.loads(payload))
                for ident, payload in conn.execute("SELECT id,data FROM packets")
            }
        expected = {str(packet["id"]): digest(packet) for packet in read(private)}
        if actual != expected or store.statistics()["assigned_judgments"] != 400:
            raise APIStopped(
                "existing human-review store differs from the actual 200-pair release; preserve it for inspection"
            )
        if store.statistics()["locked_judgments"] != 0:
            results["human_review"] = "actual submitted judgments recorded separately"
        if self.runner.audit()["issues"]:
            raise APIStopped("experimental input/token audit failed")
        write(self.root / "project/final_results.json", results)
        write(ROOT / "docs/experiment_results.json", results)
        self.status(
            "computational_deliverables_complete_human_review_pending",
            results_path=str(self.root / "project/final_results.json"),
        )
        return results
