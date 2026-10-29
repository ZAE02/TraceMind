"""Initialize and start the portable PostgreSQL cluster installed under .local."""

import secrets
import subprocess
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo

root = Path(__file__).resolve().parent.parent / ".local"
bin_dir = root / "pgsql" / "bin"
data = root / "pgdata"
password_file = root / "database-password.txt"
if not (bin_dir / "initdb.exe").exists():
    raise SystemExit(
        "Extract the EDB PostgreSQL 17.11 archive to .local/pgsql first; see docs/infrastructure.md."
    )
if not (data / "PG_VERSION").exists():
    if not password_file.exists():
        password_file.write_text(secrets.token_hex(32), encoding="ascii")
    subprocess.run(
        [
            str(bin_dir / "initdb.exe"),
            "-D",
            str(data),
            "-U",
            "tracemind",
            "--auth=scram-sha-256",
            "--pwfile=" + str(password_file),
            "-E",
            "UTF8",
        ],
        check=True,
    )
if not password_file.exists():
    raise SystemExit(
        "Existing cluster password file missing; recover the password before proceeding."
    )
status = subprocess.run(
    [str(bin_dir / "pg_ctl.exe"), "-D", str(data), "status"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
if status.returncode:
    subprocess.run(
        [
            str(bin_dir / "pg_ctl.exe"),
            "-D",
            str(data),
            "-l",
            str(root / "postgres.log"),
            "-o",
            "-h 127.0.0.1 -p 55432",
            "-w",
            "start",
        ],
        check=True,
    )
password = password_file.read_text(encoding="ascii").strip()
env_file = root.parent / ".env"
if not env_file.exists():
    connection_string = make_conninfo(
        host="127.0.0.1", port=55432, user="tracemind", password=password, dbname="tracemind"
    )
    env_file.write_text("TRACEMIND_DSN=" + connection_string + "\n", encoding="utf-8")
with psycopg.connect(
    host="127.0.0.1",
    port=55432,
    user="tracemind",
    password=password,
    dbname="postgres",
    autocommit=True,
) as conn:
    if not conn.execute("SELECT 1 FROM pg_database WHERE datname = 'tracemind'").fetchone():
        conn.execute("CREATE DATABASE tracemind")
    print(
        "PostgreSQL", conn.execute("SHOW server_version").fetchone()[0], "ready at 127.0.0.1:55432"
    )
