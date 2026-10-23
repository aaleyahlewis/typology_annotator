"""Participant annotation backend.

Participants sign in here and only ever reach their own conversations. This
server is the only Label Studio client: it creates tasks and reads/writes
annotations with a server-side API key, and serves Label Studio's open-source
editor for annotation. See README "Architecture".
"""
import asyncio
import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import annotations, db, export, extract, labeling_config, labelstudio, normalize, security
from .settings import ROOT, settings

log = logging.getLogger("typology")
STATIC = ROOT / "app" / "static"
MAX_BODY = 12 * 1024 * 1024
EXTRACTION_SLOTS = asyncio.Semaphore(3)
_background = set()


@asynccontextmanager
async def lifespan(_app):
    problems = settings.check()
    if problems:
        raise RuntimeError("Configuration problems: " + "; ".join(problems))
    labeling_config.load()
    db.init()
    db.expire_pending_imports(settings.pending_import_ttl_hours)
    yield


app = FastAPI(title="Typology annotator", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        # CSRF: state changes must be same-origin JSON requests.
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
        if not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"detail": "Expected JSON"}, status_code=415)
    response = await call_next(request)
    # The editor needs inline styles and eval; everything else is same-origin only.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'; "
        "worker-src 'self' blob:; form-action 'self'; base-uri 'none'")
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    if settings.https_only:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


app.add_middleware(SessionMiddleware, secret_key=settings.session_secret or "x" * 32, session_cookie="typology_session",
                   same_site="strict", https_only=settings.https_only, max_age=8 * 3600)


def ls():
    return labelstudio.client()


# ---------- auth ----------

def participant(request: Request):
    pid = request.session.get("pid")
    row = db.get_participant_by_id(pid) if pid else None
    if not row or row["disabled"] or request.session.get("stamp") != security.session_stamp(row["code_hash"]):
        request.session.clear()
        raise HTTPException(401, "Please sign in")
    return row["id"]


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    study_id = str(body.get("study_id", "")).strip()[:64]
    code = str(body.get("code", "")).strip()[:128]
    keys = [f"id:{study_id.lower()}", f"ip:{request.client.host if request.client else '?'}"]
    if any(db.login_blocked(k, max_failures=8 if k.startswith("id") else 30) for k in keys):
        raise HTTPException(429, "Too many attempts. Please wait 15 minutes and try again.")
    row = db.get_participant(study_id)
    ok = row is not None and not row["disabled"] and security.check_code(code, row["code_hash"])
    if not row:
        security.check_code(code, security.hash_code("timing-equaliser"))
    if not ok:
        for k in keys:
            db.record_login_failure(k)
        raise HTTPException(401, "Study ID or access code is incorrect")
    db.clear_login_failures(keys[0])
    request.session.clear()
    request.session.update({"pid": row["id"], "stamp": security.session_stamp(row["code_hash"]), "study_id": row["study_id"]})
    return {"study_id": row["study_id"]}


@app.post("/api/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request, pid=Depends(participant)):
    return {"study_id": request.session.get("study_id")}


# ---------- import: share link ----------

def _import_view(imp):
    out = {"import_id": imp["id"], "status": imp["status"], "source": imp["source"]}
    if imp["status"] == "failed":
        out["error"] = {"code": imp["error_code"], "message": imp["error_message"]}
    if imp["status"] == "imported":
        out["conversation_id"] = imp["conversation_id"]
    if imp["status"] == "ready":
        messages = json.loads(imp["extracted_json"])
        warnings = json.loads(imp["warnings_json"] or "[]")
        out["preview"] = {
            "title": imp["title"],
            "method": imp["method"],
            "message_count": len(messages),
            "complete": not any(w["code"] in extract.UNCERTAIN_CODES for w in warnings),
            "warnings": warnings,
            "messages": [{"source_index": i, "role": m["role"], "author": normalize.AUTHORS[m["role"]], "text": m["text"]}
                         for i, m in enumerate(messages)],
        }
    return out


async def _run_extraction(iid, url):
    async with EXTRACTION_SLOTS:
        try:
            result = await extract.extract(url)
            db.finish_import_extraction(iid, result.title, result.method, result.messages, result.warnings)
        except extract.ExtractionError as e:
            db.fail_import(iid, e.code, e.message)
        except Exception:
            log.exception("extraction crashed for %s", iid)  # no content in this log line
            db.fail_import(iid, "extraction_failed", "Something went wrong reading that conversation. Please try again.")


def _existing_conversation(pid, share_id):
    with db.connect() as c:
        return c.execute("SELECT id FROM conversations WHERE participant_id = ? AND share_id = ? AND status != 'deleted'",
                         (pid, share_id)).fetchone()


@app.post("/api/conversations/import")
async def import_share_link(request: Request, pid=Depends(participant)):
    body = await request.json()
    try:
        canonical, share_id = extract.validate_share_url(body.get("shared_url"))
    except extract.ExtractionError as e:
        raise HTTPException(400, e.message)
    existing = _existing_conversation(pid, share_id)
    if existing:
        return JSONResponse({"status": "duplicate", "conversation_id": existing["id"],
                             "detail": "You've already added this conversation."}, status_code=409)
    active = db.active_import_for_share(pid, share_id)
    if active:
        return {"import_id": active["id"], "status": active["status"]}
    if db.recent_import_count(pid) >= settings.import_rate_per_hour:
        raise HTTPException(429, "You've started a lot of imports recently. Please wait a while and try again.")
    iid = db.create_import(pid, "share_link", share_id)
    task = asyncio.create_task(_run_extraction(iid, canonical))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return JSONResponse({"import_id": iid, "status": "processing"}, status_code=202)


# ---------- import: ChatGPT export file (converted in the browser) ----------

@app.post("/api/conversations/import-file")
async def import_export_file(request: Request, pid=Depends(participant)):
    body = await request.json()
    conv = body.get("conversation") or {}
    raw = conv.get("messages")
    if not isinstance(raw, list) or not raw or len(raw) > normalize.MAX_MESSAGES:
        raise HTTPException(400, "That conversation has no messages we can import.")
    messages = []
    for m in raw:
        if not isinstance(m, dict) or m.get("role") not in normalize.AUTHORS or not isinstance(m.get("text"), str) \
                or not m["text"].strip() or len(m["text"]) > normalize.MAX_TEXT_CHARS:
            raise HTTPException(400, "That conversation couldn't be read.")
        messages.append({"source_id": str(m.get("source_id") or "")[:128], "role": m["role"], "text": m["text"], "create_time": None})
    if db.recent_import_count(pid) >= settings.import_rate_per_hour:
        raise HTTPException(429, "You've started a lot of imports recently. Please wait a while and try again.")
    share_id = str(conv.get("conversation_id") or "")[:64] or None
    if share_id and _existing_conversation(pid, share_id):
        return JSONResponse({"status": "duplicate", "conversation_id": _existing_conversation(pid, share_id)["id"],
                             "detail": "You've already added this conversation."}, status_code=409)
    title = str(conv.get("title") or "")[:300] or None
    warnings = [] if {m["role"] for m in messages} == {"user", "assistant"} else \
        [{"code": "one_sided", "message": "We only found messages from one side of the conversation."}]
    iid = db.create_import(pid, "export_file", share_id)
    db.finish_import_extraction(iid, title, "export_file", messages, warnings)
    return JSONResponse({"import_id": iid, "status": "ready"}, status_code=201)


@app.get("/api/imports/{iid}")
def get_import(iid: str, pid=Depends(participant)):
    imp = db.get_import(iid, pid)
    if not imp:
        raise HTTPException(404, "Not found")
    return _import_view(imp)


@app.post("/api/imports/{iid}/cancel")
def cancel_import(iid: str, pid=Depends(participant)):
    if not db.end_import(iid, pid, "cancelled"):
        raise HTTPException(409, "This import can't be cancelled.")
    return {"status": "cancelled"}


async def _create_task(cid, pid, title, dialogue, schema_version, retry=False):
    """Create the Label Studio task. On retry, first look for a task an earlier attempt
    created before failing (e.g. a timeout after Label Studio saved it), so no duplicates."""
    client = ls()
    data = normalize.task_data(cid, db.get_participant_by_id(pid)["study_id"], title, dialogue, schema_version)
    try:
        await _check_project(client)
        task_id = await client.find_task_by_conversation_id(cid) if retry else None
        if task_id is None:
            task_id = await client.create_task(data)
        db.set_task(cid, task_id)
        with db.connect() as c:
            db.audit(c, pid, "task_created", cid, f"ls_task={task_id}")
        return task_id
    except labelstudio.LabelStudioError as e:
        log.warning("Label Studio task creation failed for %s: %s (status %s)", cid, e, e.status)
        db.set_task_failed(cid)
        raise HTTPException(502, "We saved your conversation but couldn't set up annotation yet. Please try again in a moment.")


_project_checked = {"at": 0.0, "ok": False}


async def _check_project(client):
    """Refuse to create tasks if the Label Studio project uses a different XML config."""
    if time.time() - _project_checked["at"] < 300 and _project_checked["ok"]:
        return
    project = await client.get_project()
    ok = (project.get("label_config") or "").strip() == labeling_config.load().xml.strip()
    _project_checked.update(at=time.time(), ok=ok)
    if not ok:
        log.error("Label Studio project %s label_config differs from config/labeling_config.xml; run scripts/manage.py sync-config",
                  settings.ls_project_id)
        raise labelstudio.LabelStudioError("project config mismatch", 409)


@app.post("/api/imports/{iid}/confirm")
async def confirm_import(iid: str, request: Request, pid=Depends(participant)):
    body = await request.json()
    exclude = body.get("exclude") or []
    imp = db.get_import(iid, pid)
    if not imp:
        raise HTTPException(404, "Not found")
    if imp["status"] == "imported":
        return {"conversation_id": imp["conversation_id"]}
    if imp["status"] != "ready":
        raise HTTPException(409, "This import isn't ready to confirm.")
    messages = json.loads(imp["extracted_json"])
    if not isinstance(exclude, list) or not all(isinstance(i, int) and 0 <= i < len(messages) for i in exclude):
        raise HTTPException(400, "Invalid selection.")
    keep = [i for i in range(len(messages)) if i not in set(exclude)]
    if not keep:
        raise HTTPException(400, "Please keep at least one message.")
    # Generated message IDs (used only when ChatGPT's own IDs are missing) are scoped to the source conversation.
    scope = "c" + hashlib.sha256((imp["share_id"] or iid).encode()).hexdigest()[:10]
    dialogue = normalize.build_dialogue(scope, messages, keep)
    try:
        normalize.validate_dialogue(dialogue, messages)
    except normalize.NormalizationError as e:
        log.error("normalization failed for %s: %s", iid, e)
        raise HTTPException(422, "This conversation couldn't be prepared for annotation.")
    canonical = {"extracted": messages, "dialogue": dialogue, "excluded_source_indexes": sorted(set(exclude)),
                 "fingerprints": [normalize.text_fingerprint(m["text"]) for m in dialogue]}
    warnings = json.loads(imp["warnings_json"] or "[]")
    try:
        cid, created = db.claim_import_for_conversation(iid, pid, canonical, imp["title"], imp["share_id"], imp["source"],
                                                        imp["method"], warnings, settings.schema_version, labeling_config.load().sha256)
    except LookupError as e:
        if str(e).startswith("duplicate:"):
            return JSONResponse({"status": "duplicate", "conversation_id": str(e).split(":", 1)[1],
                                 "detail": "You've already added this conversation."}, status_code=409)
        raise HTTPException(409, "This import can't be confirmed.")
    if created:
        await _create_task(cid, pid, imp["title"], dialogue, settings.schema_version)
    return {"conversation_id": cid}


@app.post("/api/conversations/{cid}/retry")
async def retry_task(cid: str, pid=Depends(participant)):
    if not db.claim_task_retry(cid, pid):
        conv = db.get_conversation(cid, pid)
        if conv and conv["status"] == "ready":
            return {"conversation_id": cid}
        raise HTTPException(409, "Nothing to retry.")
    with db.connect() as c:
        db.audit(c, pid, "task_retry", cid)
    conv = db.get_conversation(cid, pid)
    await _create_task(cid, pid, conv["title"], json.loads(conv["canonical_json"])["dialogue"], conv["schema_version"], retry=True)
    return {"conversation_id": cid}


# ---------- conversations & annotation ----------

@app.get("/api/conversations")
def list_conversations(pid=Depends(participant)):
    return [dict(r) for r in db.list_conversations(pid)]


def _owned_ready(cid, pid):
    conv = db.get_conversation(cid, pid)
    if not conv:
        raise HTTPException(404, "Not found")
    if conv["status"] != "ready":
        raise HTTPException(409, "This conversation isn't ready for annotation yet.")
    return conv


@app.get("/api/conversations/{cid}")
async def get_conversation(cid: str, pid=Depends(participant)):
    conv = _owned_ready(cid, pid)
    canonical = json.loads(conv["canonical_json"])
    client = ls()
    try:
        task = await client.get_task(conv["ls_task_id"])
        ann_row = db.get_annotation(cid)
        result = None
        if ann_row and ann_row["ls_annotation_id"]:
            result = (await client.get_annotation(ann_row["ls_annotation_id"]))["result"]
    except labelstudio.LabelStudioError as e:
        log.warning("Label Studio read failed for %s: status %s", cid, e.status)
        raise HTTPException(502, "We couldn't load your annotations right now. Please try again in a moment.")
    cfg = labeling_config.load()
    data = task.get("data") or {}
    # Integrity: the task must be this participant's conversation, with unchanged text.
    task_texts = [m.get(cfg.text_key) for m in data.get(cfg.value_key) or []]
    if data.get("conversation_id") != cid or task_texts != [m["text"] for m in canonical["dialogue"]]:
        log.error("Label Studio task %s does not match conversation %s", conv["ls_task_id"], cid)
        raise HTTPException(409, "This conversation can't be opened. Please contact the research team.")
    return {"conversation_id": cid, "title": conv["title"], "data": data, "result": result,
            "labels": {"control": cfg.span_control, "user": cfg.user_label, "assistant": cfg.assistant_label},
            "version": ann_row["version"] if ann_row else 0,
            "submitted": bool(ann_row and ann_row["submitted_at"])}


@app.put("/api/conversations/{cid}/annotation")
async def save_annotation(cid: str, request: Request, pid=Depends(participant)):
    conv = _owned_ready(cid, pid)
    body = await request.json()
    dialogue = json.loads(conv["canonical_json"])["dialogue"]
    try:
        regions = annotations.validate(body.get("result"), dialogue)
    except annotations.ResultError as e:
        raise HTTPException(400, f"Not saved: {e}.")
    base = body.get("version")
    if not isinstance(base, int):
        raise HTTPException(400, "Missing version.")
    try:
        row = db.begin_annotation_save(cid, base)
    except ValueError as e:
        if str(e) == "busy":
            raise HTTPException(409, "Another save is in progress. Please try again in a moment.")
        raise HTTPException(409, "This conversation was changed in another window. Reload the page to see the latest version.")
    client = ls()
    try:
        ann_id = row["ls_annotation_id"]
        if ann_id:
            await client.update_annotation(ann_id, body["result"])
        else:
            ann_id = await client.create_annotation(conv["ls_task_id"], body["result"])
    except labelstudio.LabelStudioError as e:
        db.release_annotation_lock(cid)
        log.warning("Label Studio save failed for %s: status %s", cid, e.status)
        raise HTTPException(502, "Not saved: we couldn't reach the annotation server. Your work is still on this page; please try again.")
    submitted = bool(body.get("submitted"))
    version = db.finish_annotation_save(cid, pid, base, ann_id, submitted, regions)
    if submitted:
        # Automatic research export. A failure here never undoes the participant's save;
        # the research team can always re-export with `manage.py export`.
        try:
            export.export_submitted(cid, body["result"])
        except Exception:
            log.exception("automatic export failed for %s", cid)
            with db.connect() as c:
                db.audit(c, pid, "export_failed", cid)
    return {"ok": True, "version": version}


@app.delete("/api/conversations/{cid}")
async def delete_conversation(cid: str, pid=Depends(participant)):
    conv = db.get_conversation(cid, pid)
    if not conv:
        raise HTTPException(404, "Not found")
    if conv["ls_task_id"]:
        try:
            await ls().delete_task(conv["ls_task_id"])   # also deletes its annotations in Label Studio
        except labelstudio.LabelStudioError:
            raise HTTPException(502, "We couldn't remove it right now. Please try again.")
    db.mark_deleted(cid, pid)
    try:
        export.remove_export(db.get_participant_by_id(pid)["study_id"], cid)
    except OSError:
        log.exception("could not remove export file for %s", cid)
    return {"ok": True}


# ---------- researcher export ----------

@app.get("/admin/export")
async def admin_export(request: Request):
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not settings.admin_token or not hmac.compare_digest(supplied.encode(), settings.admin_token.encode()):
        raise HTTPException(401, "Admin token required")
    data = await export.build_export(ls())
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="typology_export.json"'})


# ---------- pages ----------

@app.get("/config/labeling_config.xml")
def labeling_xml(pid=Depends(participant)):
    return Response(labeling_config.load().xml, media_type="application/xml")


def _page(name, request, auth=True):
    if auth and not request.session.get("pid"):
        return RedirectResponse("/")
    return FileResponse(STATIC / name)


@app.get("/")
def index(request: Request):
    return _page("index.html", request, auth=False)


@app.get("/conversations")
def conversations_page(request: Request):
    return _page("conversations.html", request)


@app.get("/import/{iid}")
def import_page(iid: str, request: Request):
    return _page("import.html", request)


@app.get("/annotate/{cid}")
def annotate_page(cid: str, request: Request):
    return _page("annotate.html", request)


app.mount("/static", StaticFiles(directory=STATIC), name="static")
