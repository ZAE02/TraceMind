from tracemind.benchmark.builder import BRANCHES, BenchmarkBuilder, ScenePlan, write
from tracemind.jobs import digest


class Queue:
    def __init__(self):
        self.jobs = {}

    def enqueue(self, purpose, role, allowed_context, output_schema, parent_id=None, scope_id=None):
        request = dict(purpose=purpose, role=role, allowed_context=allowed_context)
        key = digest(request)
        self.jobs.setdefault(key, dict(id=key, request=request, status="pending"))
        return key

    def get(self, key):
        return self.jobs[key]


def plan():
    return ScenePlan(
        group_id="G001",
        theme="work_evaluation",
        split="development",
        profile={"name": "fictional", "age": 30},
        branches={
            b: {
                "sessions": [
                    dict(
                        session=i,
                        occurred_events=[f"event{i}"],
                        private_experiences=[f"private{i}"],
                        disclosure_constraints=[],
                    )
                    for i in range(1, 7)
                ]
            }
            for b in BRANCHES
        },
        shared_checkpoint_situations={
            f"S{i}": {"neutral_current_situation": "common event"} for i in (2, 4, 6)
        },
    )


def test_generation_requires_reviewed_plan(tmp_path):
    p = plan()
    write(tmp_path / "plans/G001.json", p.model_dump())
    q = Queue()
    BenchmarkBuilder(tmp_path, q).advance()
    assert not q.jobs


def test_role_context_and_future_isolation(tmp_path):
    p = plan()
    write(tmp_path / "plans/G001.json", p.model_dump())
    write(
        tmp_path / "plan_reviews/G001.json", dict(approved=True, plan_sha256=digest(p.model_dump()))
    )
    q = Queue()
    b = BenchmarkBuilder(tmp_path, q)
    b.advance()
    anchor = next(j for j in q.jobs.values() if j["request"]["purpose"] == "anchor:G001:S4")
    shared = anchor["request"]["allowed_context"]
    assert shared["common_occurred_events"] == ["event4"]
    assert shared["anchor_policy"] == "shared-current-events-v2"
    assert "private4" not in str(shared) and "event5" not in str(shared)
    first = next(j for j in q.jobs.values() if j["request"]["purpose"] == "dialogue:G001-T1:S1:U1")
    cards = first["request"]["allowed_context"]["experienced_events"]
    assert len(cards) == 1 and cards[0]["session"] == 1
    first.update(
        status="completed",
        result={"text": "A test fixture client statement"},
        result_sha256="fixture",
    )
    b.advance()
    b.advance()
    counselor = next(
        j for j in q.jobs.values() if j["request"]["purpose"] == "dialogue:G001-T1:S1:U2"
    )
    context = counselor["request"]["allowed_context"]
    assert "experienced_events" not in context and "profile" not in context
    assert "private1" not in str(context) and "event2" not in str(context)


def test_checkpoint_is_recovered_after_interrupted_write(tmp_path):
    from tracemind.benchmark.builder import read

    p = plan()
    write(tmp_path / "plans/G001.json", p.model_dump())
    write(
        tmp_path / "plan_reviews/G001.json",
        {"approved": True, "plan_sha256": digest(p.model_dump())},
    )
    rows = [
        {
            "id": f"u{i}",
            "role": "client" if i % 2 else "counselor",
            "text": f"fixture{i}",
            "job_id": f"fixture-job-{i}",
            "result_sha256": "fixture",
        }
        for i in range(1, 17)
    ]
    common = {
        "case_id": "G001-T1",
        "group_id": "G001",
        "split": "development",
        "plan_sha256": digest(p.model_dump()),
    }
    write(
        tmp_path / "dialogues/G001-T1/S01.json",
        {**common, "session": 1, "utterances": rows, "complete": True},
    )
    opening = {
        "id": "opening",
        "role": "client",
        "text": "fixture common opening",
        "job_id": "anchor",
        "result_sha256": "fixture",
    }
    write(
        tmp_path / "dialogues/G001-T1/S02.json",
        {**common, "session": 2, "utterances": [opening], "complete": False},
    )
    queue = Queue()
    queue.jobs["anchor"] = {
        "status": "completed",
        "result": {"text": opening["text"]},
        "result_sha256": "fixture",
    }
    BenchmarkBuilder(tmp_path, queue).advance()
    cp = read(tmp_path / "checkpoints/G001-T1-CP1.json")
    assert cp["latest_client"] == opening and len(cp["previous_sessions"]) == 1
    report = BenchmarkBuilder(tmp_path, Queue()).audit(verify_jobs=False)
    assert not report["issues"]
    assert report["complete_sessions"] == 1 and report["complete_trajectories"] == 0
    cp["previous_sessions"][0]["utterances"][0]["text"] = "injected future clue"
    cp["public_sha256"] = digest({k: v for k, v in cp.items() if k != "public_sha256"})
    write(tmp_path / "checkpoints/G001-T1-CP1.json", cp)
    report = BenchmarkBuilder(tmp_path, Queue()).audit(verify_jobs=False)
    assert "checkpoint public history mismatch: G001-T1-CP1" in report["issues"]
