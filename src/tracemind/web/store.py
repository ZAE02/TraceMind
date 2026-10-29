"""Durable local review repository. Each write is committed atomically."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from tracemind.evaluation.packets import public_packet

FIELDS = ("unsupported_assertion", "leading_question", "tentative_exploration", "missed_progress")


class ReviewStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS reviewers (id TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS packets (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assignments (reviewer TEXT NOT NULL REFERENCES reviewers(id), packet TEXT NOT NULL REFERENCES packets(id), PRIMARY KEY(reviewer, packet));
            CREATE TABLE IF NOT EXISTS reviews (reviewer TEXT NOT NULL, packet TEXT NOT NULL, data TEXT NOT NULL, locked INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(reviewer, packet), FOREIGN KEY(reviewer,packet) REFERENCES assignments(reviewer,packet));""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def initialize(self, reviewers: dict[str, str], packets: list[dict[str, Any]]) -> None:
        if len(reviewers) != 2 or any(not k.strip() or not v for k, v in reviewers.items()):
            raise ValueError("exactly two distinct reviewer identities and passwords required")
        with self.connect() as db:
            if db.execute("SELECT COUNT(*) FROM reviewers").fetchone()[0]:
                raise ValueError("store already initialized")
            for identity, password in reviewers.items():
                salt = secrets.token_hex(16)
                digest = hashlib.pbkdf2_hmac(
                    "sha256", password.encode(), bytes.fromhex(salt), 200000
                ).hex()
                db.execute("INSERT INTO reviewers VALUES (?,?,?)", (identity, salt, digest))
            for packet in packets:
                db.execute(
                    "INSERT INTO packets VALUES (?,?)",
                    (str(packet["id"]), json.dumps(packet, ensure_ascii=False)),
                )
                for identity in reviewers:
                    db.execute(
                        "INSERT INTO assignments VALUES (?,?)", (identity, str(packet["id"]))
                    )

    def authenticate(self, identity: str, password: str) -> bool:
        with self.connect() as db:
            row = db.execute(
                "SELECT salt,password_hash FROM reviewers WHERE id=?", (identity,)
            ).fetchone()
        if not row:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(row[0]), 200000
        ).hex()
        return hmac.compare_digest(digest, row[1])

    def assignments(self, identity: str) -> list[tuple[str, bool]]:
        with self.connect() as db:
            return [
                (packet, bool(locked))
                for packet, locked in db.execute(
                    "SELECT a.packet, COALESCE(r.locked,0) FROM assignments a LEFT JOIN reviews r ON a.reviewer=r.reviewer AND a.packet=r.packet WHERE a.reviewer=? ORDER BY CAST(a.packet AS INTEGER)",
                    (identity,),
                )
            ]

    def get(
        self, identity: str, packet_id: str
    ) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
        with self.connect() as db:
            row = db.execute(
                "SELECT p.data,r.data,r.locked FROM assignments a JOIN packets p ON p.id=a.packet LEFT JOIN reviews r ON r.reviewer=a.reviewer AND r.packet=a.packet WHERE a.reviewer=? AND a.packet=?",
                (identity, packet_id),
            ).fetchone()
        if not row:
            raise KeyError(packet_id)
        return (
            public_packet(json.loads(row[0])),
            json.loads(row[1]) if row[1] else None,
            bool(row[2]),
        )

    def save(self, identity: str, packet_id: str, data: dict[str, Any], lock: bool = False) -> None:
        if data.get("preference") not in (None, "a", "b", "tie") or (
            lock and data.get("preference") is None
        ):
            raise ValueError("select a response preference or tie before locking")
        for side in ("a", "b"):
            if side not in data or any(
                data[side].get(f) not in (True, False, None) for f in FIELDS
            ):
                raise ValueError("invalid review")
            if lock and (
                any(data[side].get(f) is None for f in FIELDS)
                or not str(data[side].get("evidence", "")).strip()
                or not str(data[side].get("explanation", "")).strip()
            ):
                raise ValueError("complete all fields, evidence and explanation before locking")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM assignments WHERE reviewer=? AND packet=?", (identity, packet_id)
            ).fetchone():
                raise KeyError(packet_id)
            row = db.execute(
                "SELECT locked FROM reviews WHERE reviewer=? AND packet=?", (identity, packet_id)
            ).fetchone()
            if row and row[0]:
                raise ValueError("review is locked")
            db.execute(
                "INSERT INTO reviews VALUES (?,?,?,?) ON CONFLICT(reviewer,packet) DO UPDATE SET data=excluded.data,locked=excluded.locked",
                (identity, packet_id, json.dumps(data, ensure_ascii=False), int(lock)),
            )

    def revealed(self) -> bool:
        with self.connect() as db:
            reviewers = db.execute("SELECT COUNT(*) FROM reviewers").fetchone()[0]
            total, locked = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(COALESCE(r.locked,0)),0) FROM assignments a LEFT JOIN reviews r ON r.reviewer=a.reviewer AND r.packet=a.packet"
            ).fetchone()
        return reviewers == 2 and total > 0 and total == locked

    def mappings(self) -> dict[str, Any]:
        if not self.revealed():
            raise PermissionError("all assigned reviews must be locked")
        with self.connect() as db:
            return {
                p: json.loads(data)["mapping"]
                for p, data in db.execute("SELECT id,data FROM packets")
            }
