"""Server-rendered local human review application."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .store import FIELDS, ReviewStore


def create_app(store: ReviewStore, secret: str, evidence_provider=None) -> FastAPI:
    if len(secret) < 32:
        raise ValueError("session secret must have at least 32 characters")
    app = FastAPI()
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    def token(identity: str) -> str:
        sig = hmac.new(secret.encode(), identity.encode(), hashlib.sha256).hexdigest()
        return identity.encode().hex() + "." + sig

    def identity(request: Request) -> str:
        try:
            raw, sig = request.cookies["review_session"].split(".", 1)
            name = bytes.fromhex(raw).decode()
            if not hmac.compare_digest(token(name).split(".")[1], sig):
                raise ValueError()
            return name
        except (KeyError, ValueError, UnicodeError):
            raise HTTPException(401, "Sign in required")

    async def form(request: Request) -> dict[str, str]:
        return {
            k: v[-1]
            for k, v in parse_qs((await request.body()).decode(), keep_blank_values=True).items()
        }

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return templates.TemplateResponse(request=request, name="login.html", context={})

    @app.post("/login")
    async def login(request: Request):
        data = await form(request)
        if not store.authenticate(data.get("identity", ""), data.get("password", "")):
            raise HTTPException(401, "Invalid credentials")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            "review_session", token(data["identity"]), httponly=True, samesite="strict"
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        name = identity(request)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "reviewer": name,
                "assignments": store.assignments(name),
                "revealed": store.revealed(),
            },
        )

    @app.get("/packet/{packet_id}", response_class=HTMLResponse)
    def packet(request: Request, packet_id: str):
        try:
            p, review, locked = store.get(identity(request), packet_id)
        except KeyError:
            raise HTTPException(404)
        return templates.TemplateResponse(
            request=request,
            name="packet.html",
            context={"packet": p, "review": review or {}, "locked": locked, "fields": FIELDS},
        )

    @app.post("/packet/{packet_id}")
    async def submit(request: Request, packet_id: str):
        name, f = identity(request), await form(request)
        review = {"preference": f.get("preference") or None}
        for side in ("a", "b"):
            review[side] = {
                key: {"yes": True, "no": False}.get(f.get(side + "_" + key)) for key in FIELDS
            }
            review[side].update(
                {key: f.get(side + "_" + key, "") for key in ("evidence", "explanation")}
            )
        try:
            store.save(name, packet_id, review, lock=f.get("action") == "lock")
        except KeyError:
            raise HTTPException(404)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return RedirectResponse("/packet/" + packet_id, status_code=303)

    @app.get("/viewer", response_class=HTMLResponse)
    def viewer(request: Request):
        identity(request)
        if evidence_provider is None:
            raise HTTPException(404)
        if not store.revealed():
            raise HTTPException(403, "Viewer available after all reviews are locked")
        return templates.TemplateResponse(
            request=request, name="viewer.html", context={"content": evidence_provider()}
        )

    @app.get("/mapping")
    def mapping(request: Request):
        identity(request)
        try:
            return store.mappings()
        except PermissionError as exc:
            raise HTTPException(403, str(exc))

    return app
