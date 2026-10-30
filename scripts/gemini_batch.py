"""Run exactly one API batch. No automatic next batch."""

import argparse
import json

from tracemind.api_batch import run_batch

parser = argparse.ArgumentParser()
parser.add_argument("--batch-id", required=True)
parser.add_argument("--limit", type=int, default=20)
args = parser.parse_args()
result = run_batch(args.batch_id, args.limit)
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["status"] == "completed" else 1)
