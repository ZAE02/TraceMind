"""PostgreSQL durable outbox; an active Codex session supplies model results."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .config import MODEL, REASONING


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate_job_result(schema: dict, result: dict) -> None:
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(result)


class JobQueue:
    def __init__(self, dsn: str, model: str = MODEL, reasoning: str = REASONING):
        self.dsn = dsn
        self.model, self.reasoning = model, reasoning

    def setup(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS agent_jobs (
              id text PRIMARY KEY, request jsonb NOT NULL, parent_id text,
              status text NOT NULL DEFAULT 'pending', result jsonb,
              provenance jsonb, result_sha256 text, created_at timestamptz DEFAULT now(),
              completed_at timestamptz, CHECK(status IN ('pending','completed','failed')) )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS agent_scope_bindings (
                agent_task text PRIMARY KEY, scope_id text NOT NULL, role text NOT NULL)""")

    def enqueue(
        self,
        purpose: str,
        role: str,
        allowed_context: dict,
        output_schema: dict,
        parent_id: str | None = None,
        scope_id: str | None = None,
    ) -> str:
        request = dict(
            purpose=purpose,
            role=role,
            allowed_context=allowed_context,
            output_schema=output_schema,
            model=self.model,
            reasoning=self.reasoning,
            parent_id=parent_id,
        )
        if scope_id is not None:
            if not isinstance(scope_id, str) or not scope_id.strip():
                raise ValueError("scope_id must be a nonempty string")
            request["scope_id"] = scope_id
        if not isinstance(role, str) or not role.strip():
            raise ValueError("role must be a nonempty string")
        job_id = digest(request)
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                "INSERT INTO agent_jobs(id,request,parent_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (job_id, Jsonb(request), parent_id),
            )
        return job_id

    def get(self, job_id: str) -> dict:
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT id,request,status,result,provenance,result_sha256 FROM agent_jobs WHERE id=%s",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return dict(zip(("id", "request", "status", "result", "provenance", "result_sha256"), row))

    def stats(self) -> list[dict]:
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT status,request->>'purpose',count(*) FROM agent_jobs GROUP BY status,request->>'purpose' ORDER BY status,request->>'purpose'"
            ).fetchall()
        return [
            dict(status=status, purpose=purpose, count=count) for status, purpose, count in rows
        ]

    def get_many(self, job_ids: list[str]) -> dict[str, dict]:
        if not job_ids:
            return {}
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT id,request,status,result,provenance,result_sha256 FROM agent_jobs WHERE id=ANY(%s)",
                (job_ids,),
            ).fetchall()
        return {
            row[0]: dict(
                zip(("id", "request", "status", "result", "provenance", "result_sha256"), row)
            )
            for row in rows
        }

    def pending(self, limit: int = 20) -> list[dict]:
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT id,request FROM agent_jobs WHERE status='pending' ORDER BY created_at,id LIMIT %s",
                (limit,),
            ).fetchall()
        return [dict(job_id=row[0], **row[1]) for row in rows]

    def complete(self, job_id: str, result: dict, provenance: dict):
        api_result = provenance.get("provider") == "openai-compatible"
        identity = provenance.get("execution_id") if api_result else provenance.get("agent_task")
        if not isinstance(result, dict) or not isinstance(identity, str) or not identity.strip():
            raise ValueError(
                "a result object and actual agent task or API execution identity are required"
            )
        sha = digest(result)
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT result_sha256, request, provenance FROM agent_jobs WHERE id=%s FOR UPDATE",
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if (
                provenance.get("model") != row[1]["model"]
                or provenance.get("reasoning") != row[1]["reasoning"]
            ):
                raise ValueError("result model and reasoning must match the original job request")
            if api_result != (row[1]["reasoning"] == "provider-default"):
                raise ValueError("job execution provider mismatch")
            validate_job_result(row[1]["output_schema"], result)
            if row[0]:
                if row[0] != sha or row[2] != provenance:
                    raise ValueError("completed result and provenance are immutable")
                return
            scope_id = row[1].get("scope_id", f"job:{job_id}")
            role = row[1]["role"]
            actor = "api:" + identity if api_result else identity
            conn.execute(
                "INSERT INTO agent_scope_bindings (agent_task,scope_id,role) VALUES (%s,%s,%s) ON CONFLICT (agent_task) DO NOTHING",
                (actor, scope_id, role),
            )
            binding = conn.execute(
                "SELECT scope_id,role FROM agent_scope_bindings WHERE agent_task=%s FOR UPDATE",
                (actor,),
            ).fetchone()
            if binding != (scope_id, role):
                raise ValueError("agent task is already bound to a different scope or role")
            conn.execute(
                "UPDATE agent_jobs SET status='completed',result=%s,provenance=%s,result_sha256=%s,completed_at=coalesce(completed_at,now()) WHERE id=%s",
                (Jsonb(result), Jsonb(provenance), sha, job_id),
            )

    def export(self, destination: Path, limit: int = 20):
        destination.mkdir(parents=True, exist_ok=True)
        for job in self.pending(limit):
            (destination / (job["job_id"] + ".json")).write_text(
                json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8"
            )
