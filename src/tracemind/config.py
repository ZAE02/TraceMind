import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / ".local"
MODEL = "gpt-6.1-terra"
REASONING = "medium"


def dsn() -> str:
    value = os.environ.get("TRACEMIND_DSN")
    if not value:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("TRACEMIND_DSN="):
                    value = line.split("=", 1)[1].strip().strip('"')
    if not value:
        raise RuntimeError("TRACEMIND_DSN is required; run scripts/setup_database.ps1")
    return value
