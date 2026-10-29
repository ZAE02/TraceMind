"""Regenerate the 192 frozen P0 tasks with Qwen Flash on identical inputs."""
import concurrent.futures
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "p0_understanding_in_action"))
import run_p0 as original

original.ROOT = ROOT / "source_p0"
original.GEN_MODEL = "qwen3.7-flash-2026-07-15"


def one(secret, name):
    original.answer_node(secret, name[:3], name[4:])


def main():
    secret = os.environ.get("DASHSCOPE_API_KEY") or sys.stdin.readline().strip()
    if not secret:
        raise RuntimeError("Missing DASHSCOPE_API_KEY")
    jobs = ["P%02d_CP%d" % (i, j) for i in range(1, 9) for j in range(1, 4)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for future in concurrent.futures.as_completed([pool.submit(one, secret, job) for job in jobs]):
            future.result()
    result = original.audit()
    if not result["complete"]:
        raise RuntimeError("Answer audit failed: " + repr(result["issues"][:8]))
    print("Qwen Flash answers audited: 192", flush=True)


if __name__ == "__main__":
    main()
