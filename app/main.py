"""Participant-facing annotation app: import a ChatGPT conversation, annotate it, export for analysis."""
import hashlib
import hmac
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import db, export, share_link
from .validate import ValidationError, clean_conversation, clean_result

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
CONFIG = ROOT / "config" / "labeling_config.xml"

@asynccontextmanager
async def lifespan(_app):
    db.init()
    yield


app = FastAPI(title="Typology annotator", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET") or secrets.token_hex(32),
    same_site="strict",
    https_only=os.environ.get("HTTPS_ONLY", "0") == "1",
    max_age=8 * 3600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # The editor needs inline styles and eval; everything else is same-origin only.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'; "
        "worker-src 'self' blob:")
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


# ---------- auth ----------

def hash_code(code, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(code.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + ":" + digest.hex()


def check_code(code, stored):
    salt, digest = stored.split(":")
    return hmac.compare_digest(hash_code(code, bytes.fromhex(salt)).split(":")[1], digest)


def participant(request: Request):
    pid = request.session.get("participant_id")
    if not pid:
        raise HTTPException(401, "Please sign in")
    return pid


def require_json(request: Request):
    # Cross-site forms cannot send application/json, which blocks CSRF on state-changing calls.
    if request.method in ("POST", "PUT", "DELETE") and not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(415, "Expected JSON")


@app.post("/api/login", dependencies=[Depends(require_json)])
async def login(request: Request):
    body = await request.json()
    row = db.get_participant(str(body.get("study_id", "")).strip())
    if not row or not check_code(str(body.get("code", "")).strip(), row["code_hash"]):
        raise HTTPException(401, "Study ID or access code is incorrect")
    request.session.clear()
    request.session["participant_id"] = row["id"]
    request.session["study_id"] = row["study_id"]
    return {"study_id": row["study_id"]}


@app.post("/api/logout", dependencies=[Depends(require_json)])
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/features")
def features():
    return {"share_link_import": share_link.enabled()}


@app.get("/api/me")
def me(request: Request, pid=Depends(participant)):
    return {"study_id": request.session.get("study_id")}


# ---------- conversations ----------

@app.get("/api/tasks")
def tasks(pid=Depends(participant)):
    return [dict(r) for r in db.list_tasks(pid)]


@app.post("/api/tasks", dependencies=[Depends(require_json)])
async def create_task(request: Request, pid=Depends(participant)):
    body = await request.json()
    try:
        data = clean_conversation(body.get("conversation"))
    except ValidationError as e:
        raise HTTPException(400, str(e))
    source = body.get("source") if body.get("source") in ("export_file", "share_link") else "export_file"
    return {"id": db.create_task(pid, data, source)}


@app.post("/api/share-link", dependencies=[Depends(require_json)])
async def import_share_link(request: Request, pid=Depends(participant)):
    body = await request.json()
    if not share_link.enabled():
        raise HTTPException(503, "Link import is turned off. Please upload your ChatGPT export file instead.")
    try:
        conversation = await share_link.fetch(str(body.get("url", "")))
        data = clean_conversation(conversation)
    except (share_link.ShareLinkError, ValidationError) as e:
        raise HTTPException(422, f"{e} Please upload your ChatGPT export file instead.")
    return {"id": db.create_task(pid, data, "share_link"), "messages": len(data["dialogue"])}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int, pid=Depends(participant)):
    row = db.get_task(task_id, pid)
    if not row:
        raise HTTPException(404, "Not found")
    ann = db.get_annotation(task_id)
    return {"id": row["id"], "data": json.loads(row["data_json"]),
            "result": json.loads(ann["result_json"]) if ann else None,
            "submitted": bool(ann["submitted"]) if ann else False}


@app.delete("/api/tasks/{task_id}", dependencies=[Depends(require_json)])
def delete_task(task_id: int, pid=Depends(participant)):
    if not db.delete_task(task_id, pid):
        raise HTTPException(404, "Not found")
    return {"ok": True}


@app.put("/api/tasks/{task_id}/annotation", dependencies=[Depends(require_json)])
async def save_annotation(task_id: int, request: Request, pid=Depends(participant)):
    row = db.get_task(task_id, pid)
    if not row:
        raise HTTPException(404, "Not found")
    body = await request.json()
    try:
        result = clean_result(body.get("result"), len(json.loads(row["data_json"])["dialogue"]))
    except ValidationError as e:
        raise HTTPException(400, str(e))
    db.save_annotation(task_id, result, bool(body.get("submitted")))
    return {"ok": True}


# ---------- researcher export ----------

@app.get("/admin/export")
def admin_export(request: Request):
    token = os.environ.get("ADMIN_TOKEN")
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(supplied, token):
        raise HTTPException(401, "Admin token required")
    return JSONResponse(export.build_export(), headers={"Content-Disposition": 'attachment; filename="typology_export.json"'})


# ---------- pages ----------

@app.get("/config/labeling_config.xml")
def labeling_config(pid=Depends(participant)):
    return FileResponse(CONFIG, media_type="application/xml")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/conversations")
def conversations_page(request: Request):
    if not request.session.get("participant_id"):
        return RedirectResponse("/")
    return FileResponse(STATIC / "conversations.html")


@app.get("/annotate/{task_id}")
def annotate_page(task_id: int, request: Request):
    if not request.session.get("participant_id"):
        return RedirectResponse("/")
    return FileResponse(STATIC / "annotate.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
