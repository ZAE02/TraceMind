"""Compile the pinned official extension against the local PostgreSQL runtime."""

import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent.parent
source = root / ".local/pgvector"
vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
if not vswhere.exists():
    raise SystemExit("Install Microsoft Visual Studio 2022 C++ Build Tools first.")
visual_studio = subprocess.check_output(
    [
        str(vswhere),
        "-latest",
        "-products",
        "*",
        "-requires",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
        "-property",
        "installationPath",
    ],
    text=True,
).strip()
if not visual_studio:
    raise SystemExit("Visual Studio C++ compiler and Windows SDK are required.")
if not source.exists():
    subprocess.run(
        [
            "git",
            "clone",
            "--branch",
            "v0.8.1",
            "--depth",
            "1",
            "https://github.com/pgvector/pgvector.git",
            str(source),
        ],
        check=True,
    )
commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
if commit != "778dacf20c07caf904557a88705142631818d8cb":
    raise SystemExit("pgvector source revision differs from the verified release.")
runtime = root / ".local/pgsql"
if not (runtime / "include/server/postgres.h").exists():
    raise SystemExit("Extract PostgreSQL 17 binaries and development headers first.")
batch = root / ".local/build_pgvector.cmd"
batch.write_text(
    "\n".join(
        [
            "@echo off",
            f'call "{visual_studio}\\VC\\Auxiliary\\Build\\vcvars64.bat"',
            "if errorlevel 1 exit /b 1",
            f'set "PGROOT={runtime}"',
            f'cd /d "{source}"',
            "nmake /F Makefile.win",
            "if errorlevel 1 exit /b 1",
            "nmake /F Makefile.win install",
            "",
        ]
    ),
    encoding="utf-8",
)
subprocess.run(["cmd.exe", "/d", "/c", "call", str(batch)], check=True, cwd=root)
