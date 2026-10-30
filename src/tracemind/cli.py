"""Local operator CLI; all generative work is dispatched to external agents."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import typer

from . import config
from .jobs import JobQueue

app = typer.Typer(no_args_is_help=True)
db_app = typer.Typer(no_args_is_help=True)
jobs_app = typer.Typer(no_args_is_help=True)
workflow_app = typer.Typer(no_args_is_help=True)
memory_app = typer.Typer(no_args_is_help=True)
review_app = typer.Typer(no_args_is_help=True)
explorer_app = typer.Typer(no_args_is_help=True)
app.add_typer(explorer_app, name="explorer")
for name, sub in (
    ("db", db_app),
    ("jobs", jobs_app),
    ("workflow", workflow_app),
    ("memory", memory_app),
    ("review", review_app),
):
    app.add_typer(sub, name=name)


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _print(value):
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


@app.command()
def doctor():
    """Report database, pgvector, and local model cache readiness independently."""
    checks = {}
    try:
        import psycopg

        with psycopg.connect(config.dsn(), connect_timeout=3) as conn:
            conn.execute("SELECT 1")
            checks["database"] = "ready"
            checks["pgvector"] = (
                "ready"
                if conn.execute("SELECT 1 FROM pg_extension WHERE extname='vector'").fetchone()
                else "extension not installed"
            )
    except Exception as exc:
        checks["database"] = f"unavailable: {type(exc).__name__}"
        checks["pgvector"] = "unknown (database unavailable)"
    for name in ("BAAI/bge-m3", "BAAI/bge-reranker-v2-m3"):
        try:
            from huggingface_hub import snapshot_download

            from .retrieval.models import EMBEDDING_REVISION, RERANKER_REVISION

            snapshot_download(
                repo_id=name,
                revision=EMBEDDING_REVISION if name == "BAAI/bge-m3" else RERANKER_REVISION,
                local_files_only=True,
                cache_dir=str(config.LOCAL / "huggingface/hub"),
            )
            checks[name] = "cached (inference not tested)"
        except Exception:
            checks[name] = "not available in local cache"
    checks["legacy_agent_adapter"] = f"{config.MODEL}/{config.REASONING}; archived runs only"
    from .api_client import APIStopped, LimitedAPI

    try:
        checks["active_model"] = LimitedAPI().model + "; local configuration valid, API not called"
    except APIStopped:
        checks["active_model"] = "designated Gemini local configuration unavailable"
    proof = config.LOCAL / "verification/stack.json"
    checks["recorded_stack_smoke"] = _read(proof) if proof.exists() else "not run"
    _print(checks)


@db_app.command("init")
def db_init():
    import psycopg

    from .memory import PostgresMemoryStore
    from .storage.repository import migrate
    from .workflows import postgres_checkpointer

    dsn = config.dsn()
    migrate(dsn)
    PostgresMemoryStore(dsn).setup()
    JobQueue(dsn).setup()
    with postgres_checkpointer(dsn):
        pass
    with psycopg.connect(dsn) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS tracemind_workflow_runs (
            thread_id text PRIMARY KEY, subject_id text NOT NULL,
            pending_job_id text, status text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now())""")
    typer.echo("Database initialized")


@jobs_app.command("list")
def jobs_list(limit: int = 20):
    _print(JobQueue(config.dsn()).pending(limit))


@jobs_app.command("export")
def jobs_export(destination: Path, limit: int = 20):
    JobQueue(config.dsn()).export(destination, limit)
    typer.echo(f"Exported pending jobs to {destination}")


@jobs_app.command("import")
def jobs_import(path: Path):
    data = _read(path)
    JobQueue(config.dsn()).complete(data["job_id"], data["result"], data["provenance"])
    typer.echo("Result imported")


@memory_app.command("history")
def memory_history(subject_id: str):
    from .memory import PostgresMemoryStore

    _print([x.to_dict() for x in PostgresMemoryStore(config.dsn()).history(subject_id)])


def _record_interrupt(dsn: str, thread_id: str, subject: str, output: dict):
    import psycopg

    pauses = output.get("__interrupt__", ())
    if pauses:
        request = pauses[0].value
        queue = JobQueue(dsn, model=request["model"], reasoning=request["reasoning"])
        job = queue.enqueue(
            request["purpose"],
            request["role"],
            request["allowed_context"],
            request["output_schema"],
        )
        status = "waiting"
    else:
        job, status = None, "completed"
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """INSERT INTO tracemind_workflow_runs(thread_id,subject_id,pending_job_id,status)
            VALUES (%s,%s,%s,%s) ON CONFLICT(thread_id) DO UPDATE SET pending_job_id=excluded.pending_job_id,status=excluded.status,updated_at=now()""",
            (thread_id, subject, job, status),
        )
    return {"thread_id": thread_id, "status": status, "job_id": job}


@workflow_app.command("start")
def workflow_start(
    subject_id: str, input_path: Path, allowed_ids_path: Path, thread_id: str = typer.Option(None)
):
    from .memory import PostgresMemoryStore
    from .workflows import build_workflow, initial_state, postgres_checkpointer

    dsn = config.dsn()
    thread_id = thread_id or str(uuid4())
    cfg = {"configurable": {"thread_id": thread_id}}
    with postgres_checkpointer(dsn) as saver:
        graph = build_workflow(
            PostgresMemoryStore(dsn),
            saver,
            _workflow_retrieval(dsn),
            _workflow_tokenizer(),
            _workflow_resolver(dsn),
        )
        if graph.get_state(cfg).values:
            raise typer.BadParameter("thread already exists")
        output = graph.invoke(
            initial_state(
                subject_id,
                _read(input_path),
                _read(allowed_ids_path),
                model="gemini-3.8-flash",
                reasoning="provider-default",
            ),
            cfg,
        )
        _print(_record_interrupt(dsn, thread_id, subject_id, output))


@workflow_app.command("run-api")
def workflow_run_api(
    subject_id: str, input_path: Path, allowed_ids_path: Path, thread_id: str = typer.Option(None)
):
    """Run/resume the durable graph through the designated, globally limited API."""
    from .api_client import LimitedAPI, single_flight
    from .benchmark.builder import write
    from .jobs import digest
    from .retrieval.models import BGEModels
    from .workflows.api import run_api_workflow

    thread_id = thread_id or str(uuid4())
    directory = config.LOCAL / "workflows" / digest(thread_id)
    api = LimitedAPI()
    with single_flight(config.LOCAL / "api/batch.lock"):
        if not (directory / "models.json").exists():
            write(directory / "models.json", api.models("workflow:" + thread_id))
        result = run_api_workflow(
            JobQueue(config.dsn(), model=api.model, reasoning="provider-default"),
            api,
            thread_id=thread_id,
            subject_id=subject_id,
            input=_read(input_path),
            allowed_ids=_read(allowed_ids_path),
            models=BGEModels(),
            directory=directory,
        )
    _print(result)


@workflow_app.command("resume")
def workflow_resume(thread_id: str):
    import psycopg
    from langgraph.types import Command

    from .memory import PostgresMemoryStore
    from .workflows import build_workflow, postgres_checkpointer

    dsn = config.dsn()
    cfg = {"configurable": {"thread_id": thread_id}}
    with postgres_checkpointer(dsn) as saver:
        graph = build_workflow(
            PostgresMemoryStore(dsn),
            saver,
            _workflow_retrieval(dsn),
            _workflow_tokenizer(),
            _workflow_resolver(dsn),
        )
        snapshot = graph.get_state(cfg)
        if not snapshot.values:
            raise typer.BadParameter("unknown thread")
        subject = snapshot.values["subject_id"]
        interrupts = [interrupt for task in snapshot.tasks for interrupt in task.interrupts]
        if not interrupts:
            _print(_record_interrupt(dsn, thread_id, subject, {}))
            return
        # Reconstruct the queue link from the checkpoint, including after a crash
        # between checkpoint persistence and registration of the interrupt.
        state = _record_interrupt(dsn, thread_id, subject, {"__interrupt__": interrupts})
        with psycopg.connect(dsn) as conn:
            row = conn.execute(
                "SELECT status,result FROM agent_jobs WHERE id=%s", (state["job_id"],)
            ).fetchone()
        if not row or row[0] != "completed":
            _print(state)
            return
        output = graph.invoke(Command(resume=row[1]), cfg)
        _print(_record_interrupt(dsn, thread_id, subject, output))


@review_app.command("init")
def review_init(database: Path, packets: Path, reviewer_a: str, reviewer_b: str):
    from .web.store import ReviewStore

    database.parent.mkdir(parents=True, exist_ok=True)
    a = typer.prompt(f"Password for {reviewer_a}", hide_input=True, confirmation_prompt=True)
    b = typer.prompt(f"Password for {reviewer_b}", hide_input=True, confirmation_prompt=True)
    ReviewStore(database).initialize({reviewer_a: a, reviewer_b: b}, _read(packets))
    typer.echo("Review store initialized")


@review_app.command("serve")
def review_serve(database: Path, host: str = "127.0.0.1", port: int = 8000):
    import uvicorn

    from .web.app import create_app
    from .web.store import ReviewStore

    secret = os.environ.get("TRACEMIND_REVIEW_SECRET", "")
    uvicorn.run(create_app(ReviewStore(database), secret), host=host, port=port)


@review_app.command("report")
def review_report(database: Path):
    from .evaluation.human import human_report
    from .web.store import ReviewStore

    if not database.exists():
        raise typer.BadParameter("human review store does not exist")
    _print(human_report(ReviewStore(database)))


@review_app.command("export-packets")
def review_export_packets(root: Path, destination: Path):
    from .evaluation.export import export_packets

    if not destination.resolve().is_relative_to(config.LOCAL.resolve()):
        raise typer.BadParameter("held-out packets and mappings must stay under ignored .local/")
    _print(export_packets(root, destination, JobQueue(config.dsn())))


@explorer_app.command("serve")
def explorer_serve(port: int = 8001):
    import uvicorn

    from .web.explorer import create_explorer

    uvicorn.run(
        create_explorer(config.dsn(), os.environ.get("TRACEMIND_EXPLORER_SECRET", "")),
        host="127.0.0.1",
        port=port,
    )


benchmark_app = typer.Typer(no_args_is_help=True)
app.add_typer(benchmark_app, name="benchmark")


@benchmark_app.command("init")
def benchmark_init(root: Path, plans: Path):
    """Initialize a benchmark from a JSON array of explicit scene plans."""
    from .benchmark.builder import ScenePlan, write

    values = _read(plans)
    if not isinstance(values, list) or not values:
        raise typer.BadParameter("plans must be a non-empty JSON array")
    parsed = [ScenePlan.model_validate(item) for item in values]
    if len({plan.group_id for plan in parsed}) != len(parsed):
        raise typer.BadParameter("duplicate group IDs")
    for plan in parsed:
        target = root / "plans" / (plan.group_id + ".json")
        value = plan.model_dump(mode="json")
        if target.exists() and _read(target) != value:
            raise typer.BadParameter(f"existing plan differs: {target}")
        if not target.exists():
            write(target, value)
    typer.echo(f"Initialized {len(parsed)} plans")


def _runner(root: Path):
    from .experiments import ExperimentRunner
    from .retrieval.models import BGEModels
    from .retrieval.pipeline import Retriever
    from .storage.repository import EvidenceRepository

    models = BGEModels()
    repository = EvidenceRepository(config.dsn())
    from .memory import PostgresMemoryStore

    manifest = root / "experiment_manifest.json"
    experimental_queue = JobQueue(config.dsn())
    if manifest.exists():
        declared = json.loads(manifest.read_text(encoding="utf-8"))
        experimental_queue = JobQueue(
            config.dsn(), model=declared["answer_model"], reasoning=declared["reasoning"]
        )
    return ExperimentRunner(
        root,
        experimental_queue,
        repository,
        Retriever(repository, models),
        models,
        PostgresMemoryStore(config.dsn()),
    )


@benchmark_app.command("advance")
def benchmark_advance(
    root: Path,
    experiments: bool = typer.Option(
        False, help="Advance experiment candidates after evidence is indexed"
    ),
):
    from .benchmark.builder import BenchmarkBuilder

    builder = BenchmarkBuilder(root, JobQueue(config.dsn()))
    result = {"generation": builder.advance()}
    if experiments:
        result["experiments"] = _runner(root).advance()
    _print(result)


@benchmark_app.command("audit")
def benchmark_audit(root: Path, experiments: bool = typer.Option(False)):
    from .benchmark.builder import BenchmarkBuilder

    result = {"generation": BenchmarkBuilder(root, JobQueue(config.dsn())).audit()}
    if experiments:
        result["experiments"] = _runner(root).audit()
    _print(result)


@benchmark_app.command("index")
def benchmark_index(root: Path):
    """Embed newly completed dialogue utterances with the pinned BGE model."""
    from .experiments.indexing import index_dialogues
    from .retrieval.models import BGEModels
    from .storage.repository import EvidenceRepository

    _print(index_dialogues(root, EvidenceRepository(config.dsn()), BGEModels()))


@benchmark_app.command("export-development")
def benchmark_export_development(root: Path, destination: Path, limit: int = 4):
    from .benchmark.export import export_development

    _print(export_development(root, destination, limit, JobQueue(config.dsn())))


def _workflow_retrieval(dsn: str):
    from .retrieval.models import BGEModels
    from .retrieval.pipeline import Retriever
    from .storage.repository import EvidenceRepository
    from .workflows import retrieval_callback

    return retrieval_callback(Retriever(EvidenceRepository(dsn), BGEModels()))


def _workflow_tokenizer():
    from .retrieval.models import BGEModels

    return BGEModels()


def _workflow_resolver(dsn: str):
    from .storage.repository import EvidenceRepository
    from .workflows.graph import authoritative_evidence_resolver

    return authoritative_evidence_resolver(EvidenceRepository(dsn))


@benchmark_app.command("delivery-audit")
def benchmark_delivery_audit(root: Path):
    """Verify full computational coverage from saved source jobs; makes no API call."""
    from .experiments.completion import audit_delivery

    result = audit_delivery(root, JobQueue(config.dsn()))
    _print(result)
    if not result["complete"]:
        raise typer.Exit(code=1)


@benchmark_app.command("freeze")
def benchmark_freeze(root: Path, refs: Path):
    """Freeze a protocol using independently reviewed calibration job references."""
    from .experiments.protocol import freeze_protocol

    declared_path = root / "experiment_manifest.json"
    queue = JobQueue(config.dsn())
    if declared_path.exists():
        declared = _read(declared_path)
        queue = JobQueue(
            config.dsn(), model=declared["answer_model"], reasoning=declared["reasoning"]
        )
    manifest = freeze_protocol(root, queue, _read(refs))
    _print(
        {
            "status": manifest["status"],
            "protocol_sha256": manifest["protocol_sha256"],
            "examples": len(manifest["examples"]),
        }
    )


@benchmark_app.command("report")
def benchmark_report(root: Path, split: str = "test"):
    from .evaluation.metrics import collect_metric_rows
    from .evaluation.paired import paired_report

    rows = collect_metric_rows(root, split, JobQueue(config.dsn()))
    if not rows:
        _print(
            {
                "split": split,
                "status": "pending",
                "completed_quality_and_access_rows": 0,
                "outcomes": None,
            }
        )
    else:
        _print(
            {
                "split": split,
                "review_kind": "model_not_human",
                "completed_quality_and_access_rows": len(rows),
                "outcomes": paired_report(rows),
                "caveat": "Conditional paired judgments; small base-scene samples and same-family model bias.",
            }
        )
