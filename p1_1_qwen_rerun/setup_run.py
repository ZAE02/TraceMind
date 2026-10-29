"""Prepare an isolated P1 rerun while preserving the original P0/P1 outputs."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
SOURCE = PROJECT / "p0_understanding_in_action"
P1 = PROJECT / "p1_evaluation"
MIRROR = ROOT / "source_p0"


def copy_once(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)
    elif source.read_bytes() != target.read_bytes():
        raise RuntimeError("Prepared source changed: " + str(target))


for name in ("p1.py", "mechanisms.py", "judge_mechanisms.py", "budget_match.py",
             "full_access.py", "analyze.py", "analyze_mechanisms.py", "audit_p1.py"):
    copy_once(P1 / name, ROOT / name)

for folder in ("plans", "dialogues", "checkpoints/public", "checkpoints/annotation_drafts", "inputs"):
    for file in (SOURCE / folder).rglob("*.json"):
        copy_once(file, MIRROR / file.relative_to(SOURCE))
for file in (SOURCE / "retrieval").glob("P*_CP*.json"):
    copy_once(file, MIRROR / file.relative_to(SOURCE))
copy_once(SOURCE / "review/internal_blind_key.json", MIRROR / "review/internal_blind_key.json")
print("Prepared isolated sources and P1 scripts", flush=True)
