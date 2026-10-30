"""Run authorized synthesis up to an optional limit or the approved dataset target."""

import argparse
import json

from tracemind.api_batch import run_batch

parser = argparse.ArgumentParser()
parser.add_argument("--batch-id", required=True)
parser.add_argument(
    "--limit", type=int, default=None, help="Omit to finish all approved scene trajectories"
)
args = parser.parse_args()
result = run_batch(args.batch_id, args.limit)
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["status"] == "completed" else 1)
