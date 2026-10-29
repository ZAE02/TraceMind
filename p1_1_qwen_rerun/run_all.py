"""Resumable, sequential orchestration of the complete rerun."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
secret = sys.stdin.readline().strip()
if not secret:
    raise SystemExit("Missing API key on stdin")
env = os.environ.copy()
env["DASHSCOPE_API_KEY"] = secret
steps = [
    ("generate_answers.py",),
    ("p1.py", "freeze"),
    ("p1.py", "annotations"),
    ("p1.py", "review"),
    ("p1.py", "second_review"),
    ("analyze.py",),
    ("mechanisms.py", "rolling"),
    ("mechanisms.py", "c4"),
    ("mechanisms.py", "responses"),
    ("judge_mechanisms.py", "prepare"),
    ("judge_mechanisms.py", "review"),
    ("budget_match.py", "compress"),
    ("budget_match.py", "answers"),
    ("budget_match.py", "prepare"),
    ("budget_match.py", "review"),
    ("full_access.py", "formulations"),
    ("full_access.py", "answers"),
    ("full_access.py", "prepare"),
    ("full_access.py", "review"),
    ("analyze_mechanisms.py",),
    ("audit_p1.py",),
]
for step in steps:
    print("RUN", " ".join(step), flush=True)
    subprocess.run([sys.executable, "-u", *step], cwd=ROOT, env=env, check=True)
print("Complete", flush=True)
