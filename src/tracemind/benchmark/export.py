"""Only reviewed development sessions can enter the public example release."""

from pathlib import Path

from tracemind.jobs import digest

from .builder import read, write


def export_development(root: Path, destination: Path, limit: int = 4, queue=None):
    if limit < 1:
        raise ValueError("limit must be positive")
    exported = []
    for path in sorted((root / "dialogues").glob("*/S*.json")):
        session = read(path)
        if session.get("split") != "development" or not session.get("complete"):
            continue
        if len(session["utterances"]) != 16 or any(
            u["role"] != ("client" if i % 2 == 0 else "counselor")
            for i, u in enumerate(session["utterances"])
        ):
            raise ValueError("published sessions require eight complete alternating exchanges")
        review_path = (
            root / "dialogue_reviews" / f"{session['case_id']}_S{session['session']:02}.json"
        )
        if not review_path.exists():
            continue
        review = read(review_path)
        expected = digest(session)
        if review.get("approved") is not True or review.get("dialogue_sha256") != expected:
            continue
        manifest = root / "dataset.json"
        if manifest.exists() and read(manifest).get("namespace") == "gemini_v1":
            if queue is None:
                raise ValueError(
                    "Gemini example release requires verification against actual review jobs"
                )
            source = queue.get(review["job_id"])
            if (
                source["status"] != "completed"
                or source["result_sha256"] != review["result_sha256"]
                or digest({key: review[key] for key in ("approved", "issues")})
                != source["result_sha256"]
                or source["request"]["role"] != "independent session quality reviewer"
                or source["request"]["allowed_context"]["dialogue"]
                != [
                    {key: row[key] for key in ("id", "role", "text")}
                    for row in session["utterances"]
                ]
            ):
                raise ValueError("session review release provenance mismatch")
        public = {
            "case_id": session["case_id"],
            "session": session["session"],
            "synthetic": True,
            "review": "model_reviewed_not_human",
            "source_sha256": expected,
            "utterances": [
                {k: u[k] for k in ("id", "role", "text", "job_id", "result_sha256")}
                for u in session["utterances"]
            ],
        }
        if review.get("job_id"):
            public["review_source_job_id"] = review["job_id"]
        target = destination / session["case_id"] / path.name
        if target.exists() and read(target) != public:
            raise ValueError("published example changed; create a new release version")
        write(target, public)
        exported.append(target.relative_to(destination).as_posix())
        if len(exported) >= limit:
            break
    write(
        destination / "manifest.json",
        {
            "source": "development only",
            "synthetic": True,
            "human_review_performed": False,
            "usage": "engineering smoke; persistent multi-case role contexts"
            if root.name == "v1"
            else "reviewed development example",
            "sessions": exported,
            "count": len(exported),
        },
    )
    return {"exported_sessions": len(exported)}
