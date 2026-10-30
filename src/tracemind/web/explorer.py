"""Local research explorer, separate from the blinded reviewer application."""

from __future__ import annotations

import hmac
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from tracemind.memory import PostgresMemoryStore
from tracemind.storage import EvidenceRepository


def create_explorer(dsn: str, secret: str) -> FastAPI:
    if len(secret) < 32:
        raise ValueError("TRACEMIND_EXPLORER_SECRET must have at least32 characters")
    app = FastAPI(title="TraceMind research explorer")
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    session = hmac.new(secret.encode(), b"research-explorer", "sha256").hexdigest()

    def authenticate(request):
        if not hmac.compare_digest(request.cookies.get("research_session", ""), session):
            raise HTTPException(401, "Research sign-in required")

    @app.get("/login", response_class=HTMLResponse)
    def login_page():
        return '<html lang="zh"><meta charset="utf-8"><title>TraceMind research login</title><form method="post"><label>Research access token <input type="password" name="token"></label><button>Sign in</button></form></html>'

    @app.post("/login")
    async def login(request: Request):
        from urllib.parse import parse_qs

        value = parse_qs((await request.body()).decode()).get("token", [""])[0]
        if not hmac.compare_digest(value, secret):
            raise HTTPException(401)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie("research_session", session, httponly=True, samesite="strict")
        return response

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        authenticate(request)
        with psycopg.connect(dsn) as conn:
            cases = conn.execute(
                "SELECT case_id,count(*) FROM evidence GROUP BY case_id ORDER BY case_id"
            ).fetchall()
        return templates.TemplateResponse(
            request=request, name="explorer.html", context={"cases": cases, "case_id": None}
        )

    @app.get("/case/{case_id}", response_class=HTMLResponse)
    def case(request: Request, case_id: str):
        authenticate(request)
        evidence = EvidenceRepository(dsn).visible(
            case_id, (10**6, 10**6), datetime.now(timezone.utc)
        )
        if not evidence:
            raise HTTPException(404, "No indexed case")
        store = PostgresMemoryStore(dsn)
        versions = []
        streams = []
        for subject in (case_id, "experiment:evolution:" + case_id):
            history = [x.to_dict() for x in store.history(subject)]
            versions.extend(history)
            if history:
                superseded = {old for s in history[-1]["statements"] for old in s["supersedes"]}
                streams.append(
                    {
                        "label": "互动工作流" if subject == case_id else "演化记忆实验",
                        "subject_id": subject,
                        "versions": history,
                        "unresolved": [
                            s
                            for s in history[-1]["statements"]
                            if s["kind"] == "unresolved_question"
                            and s["status"] not in ("superseded", "inapplicable")
                            and s["id"] not in superseded
                        ],
                    }
                )
        return templates.TemplateResponse(
            request=request,
            name="explorer.html",
            context={
                "case_id": case_id,
                "evidence": [asdict(x) for x in evidence],
                "streams": streams,
                "version_count": len(versions),
                "versions_json": json.dumps(versions, ensure_ascii=False, indent=2),
                "kind_labels": {
                    "event": "事件",
                    "psychological_state": "心理状态",
                    "hypothesis": "解释假设",
                    "unresolved_question": "待澄清问题",
                },
                "status_labels": {
                    "proposed": "待确认",
                    "supported": "获证据支持",
                    "challenged": "受挑战",
                    "superseded": "被替代",
                    "inapplicable": "暂不适用",
                },
            },
        )

    return app
