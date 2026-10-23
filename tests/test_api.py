import json

import pytest

from app import db, security
from tests.conftest import import_and_confirm, login, wait_ready
from tests.helpers import fixtures as f
from tests.test_annotations import answer, span

OTHER = "11111111-0000-4000-8000-000000000001"


def result_for(text_by_msg):
    return [span("a", 0, "neurodivergent 🙂 to get"),
            answer("a", 0, "user_why", "textarea", ["Expectations at work"], "neurodivergent 🙂 to get"),
            span("b", 1, "Point one"),
            answer("b", 1, "ai_behavior", "choices", ["Modified my language or tone"], "Point one"),
            {"id": "c", "from_name": "conversation_accessibility_context", "to_name": "dialogue", "type": "textarea",
             "value": {"text": ["Writing to my manager"]}}]


# ---------- auth ----------

def test_requires_sign_in(app_client):
    for method, path in [("get", "/api/conversations"), ("get", "/api/me"), ("get", "/config/labeling_config.xml")]:
        assert getattr(app_client, method)(path).status_code == 401
    assert app_client.post("/api/conversations/import", json={"shared_url": f.SHARE_URL}).status_code == 401
    assert app_client.get("/conversations", follow_redirects=False).status_code in (302, 307)


def test_wrong_code_and_lockout(app_client):
    for _ in range(8):
        assert app_client.post("/api/login", json={"study_id": "P01", "code": "nope"}).status_code == 401
    r = app_client.post("/api/login", json={"study_id": "P01", "code": "code-one"})
    assert r.status_code == 429


def test_disabled_participant_and_code_reset_sign_out(app_client):
    login(app_client)
    db.set_disabled("P01", True)
    assert app_client.get("/api/me").status_code == 401
    db.set_disabled("P01", False)
    login(app_client)
    db.set_code("P01", security.hash_code("new-code"))
    assert app_client.get("/api/me").status_code == 401          # old session no longer valid
    login(app_client, code="new-code")


def test_csrf_protections(app_client):
    login(app_client)
    r = app_client.post("/api/conversations/import", content="shared_url=x",
                        headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
    r = app_client.post("/api/conversations/import", json={"shared_url": f.SHARE_URL}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_security_headers(app_client):
    h = app_client.get("/").headers
    assert "default-src 'self'" in h["content-security-policy"] and h["x-frame-options"] == "DENY"
    assert h["cache-control"] == "no-store"


# ---------- import flow ----------

def test_import_preview_exclude_confirm(app_client, fake_ls):
    login(app_client)
    r = app_client.post("/api/conversations/import", json={"shared_url": f.SHARE_URL + "?x=1"})
    assert r.status_code == 202 and r.json()["status"] == "processing"
    body = wait_ready(app_client, r.json()["import_id"])
    p = body["preview"]
    assert p["message_count"] == 4 and p["complete"] and [m["text"] for m in p["messages"]] == [t for _, t in f.UNICODE_TURNS]
    assert not fake_ls.tasks                                     # nothing sent before confirmation
    cid = app_client.post(f"/api/imports/{body['import_id']}/confirm", json={"exclude": [2]}).json()["conversation_id"]
    task = next(iter(fake_ls.tasks.values()))["data"]
    assert task["conversation_id"] == cid and task["participant_id"] == "P01"
    assert [m["text"] for m in task["dialogue"]] == [f.UNICODE_TURNS[i][1] for i in (0, 1, 3)]
    assert [m["index"] for m in task["dialogue"]] == [0, 1, 2]
    assert set(task["dialogue"][0]) == {"message_id", "index", "role", "author", "text"}
    # confirming again is idempotent
    again = app_client.post(f"/api/imports/{body['import_id']}/confirm", json={"exclude": []}).json()
    assert again["conversation_id"] == cid and len(fake_ls.tasks) == 1
    # pending content is not kept after confirmation
    with db.connect() as c:
        assert c.execute("SELECT extracted_json FROM import_requests").fetchone()[0] is None


def test_duplicate_link_points_to_existing(app_client, fake_ls):
    login(app_client)
    cid = import_and_confirm(app_client)
    r = app_client.post("/api/conversations/import", json={"shared_url": f.SHARE_URL})
    assert r.status_code == 409 and r.json()["conversation_id"] == cid
    assert len(fake_ls.tasks) == 1


def test_same_link_twice_while_processing_reuses_import(app_client):
    login(app_client)
    a = app_client.post("/api/conversations/import", json={"shared_url": f.SHARE_URL}).json()
    b = app_client.post("/api/conversations/import", json={"shared_url": f.SHARE_URL}).json()
    assert a["import_id"] == b["import_id"]


@pytest.mark.parametrize("url,code", [("https://evil.example/share/x", 400), ("not a url", 400)])
def test_invalid_links(app_client, url, code):
    login(app_client)
    r = app_client.post("/api/conversations/import", json={"shared_url": url})
    assert r.status_code == code and "ChatGPT" in r.json()["detail"]


def test_extraction_failures_are_reported(app_client, fake_ls):
    login(app_client)
    iid = app_client.post("/api/conversations/import", json={"shared_url": "https://chatgpt.com/share/22222222-0000-4000-8000-000000000002"}).json()["import_id"]
    body = wait_ready(app_client, iid)
    assert body["status"] == "failed" and body["error"]["code"] == "extraction_failed"
    iid = app_client.post("/api/conversations/import", json={"shared_url": "https://chatgpt.com/share/33333333-0000-4000-8000-000000000003"}).json()["import_id"]
    assert wait_ready(app_client, iid)["error"]["code"] == "not_found"
    assert not fake_ls.tasks


def test_cancel_discards_content(app_client, fake_ls):
    login(app_client)
    iid = app_client.post("/api/conversations/import", json={"shared_url": f.SHARE_URL}).json()["import_id"]
    wait_ready(app_client, iid)
    assert app_client.post(f"/api/imports/{iid}/cancel", json={}).json()["status"] == "cancelled"
    assert app_client.post(f"/api/imports/{iid}/confirm", json={}).status_code == 409
    with db.connect() as c:
        assert c.execute("SELECT extracted_json FROM import_requests WHERE id = ?", (iid,)).fetchone()[0] is None
    assert not fake_ls.tasks


def test_import_rate_limit(app_client, monkeypatch):
    from app.settings import settings
    object.__setattr__(settings, "import_rate_per_hour", 2)
    try:
        login(app_client)
        for i in range(2):
            app_client.post("/api/conversations/import", json={"shared_url": f"https://chatgpt.com/share/4444444{i}-0000-4000-8000-000000000000"})
        r = app_client.post("/api/conversations/import", json={"shared_url": f.SHARE_URL})
        assert r.status_code == 429
    finally:
        object.__setattr__(settings, "import_rate_per_hour", 20)


def test_export_file_import_path(app_client, fake_ls):
    login(app_client)
    conv = {"conversation_id": "abc", "title": "From export", "messages": [{"source_id": "m1", "role": "user", "text": " keep  spaces "},
                                                                         {"source_id": "m2", "role": "assistant", "text": "ok"}]}
    r = app_client.post("/api/conversations/import-file", json={"conversation": conv})
    assert r.status_code == 201
    iid = r.json()["import_id"]
    cid = app_client.post(f"/api/imports/{iid}/confirm", json={}).json()["conversation_id"]
    assert fake_ls.tasks[1]["data"]["dialogue"][0]["text"] == " keep  spaces "
    bad = {"messages": [{"role": "system", "text": "x"}]}
    assert app_client.post("/api/conversations/import-file", json={"conversation": bad}).status_code == 400
    assert cid


# ---------- Label Studio failures ----------

def test_task_creation_failure_then_retry_without_duplicates(app_client, fake_ls):
    login(app_client)
    iid = app_client.post("/api/conversations/import", json={"shared_url": f.SHARE_URL}).json()["import_id"]
    wait_ready(app_client, iid)
    fake_ls.fail.add("create_task")
    r = app_client.post(f"/api/imports/{iid}/confirm", json={})
    assert r.status_code == 502
    conv = app_client.get("/api/conversations").json()[0]
    assert conv["status"] == "task_failed"
    fake_ls.fail.clear()
    assert app_client.post(f"/api/conversations/{conv['id']}/retry", json={}).status_code == 200
    assert app_client.post(f"/api/conversations/{conv['id']}/retry", json={}).status_code == 200
    assert len(fake_ls.tasks) == 1
    assert app_client.get(f"/api/conversations/{conv['id']}").status_code == 200


def test_retry_finds_task_created_before_a_timeout(app_client, fake_ls):
    login(app_client)
    iid = app_client.post("/api/conversations/import", json={"shared_url": f.SHARE_URL}).json()["import_id"]
    wait_ready(app_client, iid)
    original = fake_ls.create_task

    async def created_then_timed_out(data):
        await original(data)
        raise __import__("app.labelstudio", fromlist=["x"]).LabelStudioError("timeout", None)

    fake_ls.create_task = created_then_timed_out
    assert app_client.post(f"/api/imports/{iid}/confirm", json={}).status_code == 502
    fake_ls.create_task = original
    cid = app_client.get("/api/conversations").json()[0]["id"]
    app_client.post(f"/api/conversations/{cid}/retry", json={})
    assert len(fake_ls.tasks) == 1


def test_project_config_mismatch_blocks_task_creation(app_client, fake_ls):
    fake_ls.label_config = "<View/>"
    login(app_client)
    iid = app_client.post("/api/conversations/import", json={"shared_url": f.SHARE_URL}).json()["import_id"]
    wait_ready(app_client, iid)
    assert app_client.post(f"/api/imports/{iid}/confirm", json={}).status_code == 502
    assert not fake_ls.tasks


# ---------- annotations ----------

def test_save_reopen_edit_delete(app_client, fake_ls):
    login(app_client)
    cid = import_and_confirm(app_client)
    first = app_client.get(f"/api/conversations/{cid}").json()
    assert first["result"] is None and first["version"] == 0
    res = result_for(first["data"]["dialogue"])
    r = app_client.put(f"/api/conversations/{cid}/annotation", json={"result": res, "version": 0, "submitted": False})
    assert r.json() == {"ok": True, "version": 1}
    again = app_client.get(f"/api/conversations/{cid}").json()
    assert again["result"] == res and again["version"] == 1 and not again["submitted"]
    edited = res[:2] + res[4:]   # delete the AI highlight and its answer
    r = app_client.put(f"/api/conversations/{cid}/annotation", json={"result": edited, "version": 1, "submitted": True})
    assert r.json()["version"] == 2
    final = app_client.get(f"/api/conversations/{cid}").json()
    assert final["result"] == edited and final["submitted"]
    assert len(fake_ls.annotations) == 1                      # one Label Studio annotation, updated in place


def test_stale_version_is_rejected(app_client):
    login(app_client)
    cid = import_and_confirm(app_client)
    app_client.put(f"/api/conversations/{cid}/annotation", json={"result": [], "version": 0})
    r = app_client.put(f"/api/conversations/{cid}/annotation", json={"result": [], "version": 0})
    assert r.status_code == 409 and "another window" in r.json()["detail"]


def test_failed_save_is_not_confirmed(app_client, fake_ls):
    login(app_client)
    cid = import_and_confirm(app_client)
    fake_ls.fail.add("save")
    r = app_client.put(f"/api/conversations/{cid}/annotation", json={"result": [], "version": 0})
    assert r.status_code == 502 and r.json()["detail"].startswith("Not saved")
    fake_ls.fail.clear()
    assert app_client.put(f"/api/conversations/{cid}/annotation", json={"result": [], "version": 0}).status_code == 200


def test_invalid_result_rejected(app_client):
    login(app_client)
    cid = import_and_confirm(app_client)
    bad = [span("b", 1, "Point one", label="my text")]
    r = app_client.put(f"/api/conversations/{cid}/annotation", json={"result": bad, "version": 0})
    assert r.status_code == 400 and "AI's text" in r.json()["detail"]


def test_tampered_label_studio_task_is_refused(app_client, fake_ls):
    login(app_client)
    cid = import_and_confirm(app_client)
    fake_ls.tasks[1]["data"]["dialogue"][0]["text"] = "changed"
    assert app_client.get(f"/api/conversations/{cid}").status_code == 409


def test_delete_removes_label_studio_task(app_client, fake_ls):
    login(app_client)
    cid = import_and_confirm(app_client)
    app_client.put(f"/api/conversations/{cid}/annotation", json={"result": [], "version": 0})
    assert app_client.delete(f"/api/conversations/{cid}", headers={"content-type": "application/json"}).status_code == 200
    assert not fake_ls.tasks and not fake_ls.annotations
    assert app_client.get(f"/api/conversations/{cid}").status_code == 404
    with db.connect() as c:
        assert c.execute("SELECT canonical_json FROM conversations WHERE id = ?", (cid,)).fetchone()[0] is None
    # the same link can be imported again after deletion
    assert import_and_confirm(app_client) != cid


# ---------- isolation ----------

def test_participants_cannot_reach_each_others_data(app_client, fake_ls):
    login(app_client)
    cid = import_and_confirm(app_client)
    pending = app_client.post("/api/conversations/import", json={"shared_url": f"https://chatgpt.com/share/{OTHER}"}).json()["import_id"]
    wait_ready(app_client, pending)
    app_client.post("/api/logout", json={})
    login(app_client, "P02", "code-two")
    assert app_client.get("/api/conversations").json() == []
    checks = [
        app_client.get(f"/api/conversations/{cid}"),
        app_client.put(f"/api/conversations/{cid}/annotation", json={"result": [], "version": 0}),
        app_client.delete(f"/api/conversations/{cid}", headers={"content-type": "application/json"}),
        app_client.post(f"/api/conversations/{cid}/retry", json={}),
        app_client.get(f"/api/imports/{pending}"),
        app_client.post(f"/api/imports/{pending}/confirm", json={}),
        app_client.post(f"/api/imports/{pending}/cancel", json={}),
    ]
    assert [r.status_code for r in checks] == [404, 404, 404, 409, 404, 404, 409]
    assert all("Point" not in r.text and "neurodivergent" not in r.text for r in checks)
    assert len(fake_ls.tasks) == 1 and not fake_ls.annotations
    # P02 importing the same link gets their own separate conversation
    own = import_and_confirm(app_client)
    assert own != cid and len(fake_ls.tasks) == 2


# ---------- export ----------

def test_export_links_spans_to_messages(app_client, fake_ls):
    login(app_client)
    cid = import_and_confirm(app_client)
    res = result_for(None)
    app_client.put(f"/api/conversations/{cid}/annotation", json={"result": res, "version": 0, "submitted": True})
    assert app_client.get("/admin/export").status_code == 401
    assert app_client.get("/admin/export", headers={"Authorization": "Bearer wrong"}).status_code == 401
    exp = app_client.get("/admin/export", headers={"Authorization": "Bearer admin-secret"}).json()
    conv = exp["conversations"][0]
    assert conv["participant_id"] == "P01" and conv["validation"]["ok"] and conv["submitted_at"]
    assert conv["conversation_context"]["conversation_accessibility_context"] == "Writing to my manager"
    assert conv["conversation_context"]["conversation_additional_context"] is None
    user_span, ai_span = conv["annotations"]
    msg = conv["messages"][0]
    assert user_span["message_id"] == msg["message_id"] and user_span["message_role"] == "user"
    assert msg["text"][user_span["span"]["start"]:user_span["span"]["end"]] == "neurodivergent 🙂 to get"
    assert user_span["span"]["end_utf16"] - user_span["span"]["end"] == 1      # emoji counted twice in UTF-16
    assert user_span["answers"]["user_why"] == "Expectations at work" and user_span["answers"]["user_where"] is None
    assert "ai_role" not in user_span["answers"]
    assert ai_span["answers"]["ai_behavior"] == ["Modified my language or tone"] and ai_span["answers"]["ai_role"] is None


def test_export_flags_mismatched_spans(app_client, fake_ls):
    login(app_client)
    cid = import_and_confirm(app_client)
    app_client.put(f"/api/conversations/{cid}/annotation", json={"result": result_for(None), "version": 0})
    ann = next(iter(fake_ls.annotations.values()))
    ann["result"][0]["value"]["text"] = "edited in Label Studio"
    exp = app_client.get("/admin/export", headers={"Authorization": "Bearer admin-secret"}).json()
    assert exp["summary"]["conversations_with_issues"] == [cid]
    assert {"region_id": "a", "problem": "text_mismatch"} in exp["conversations"][0]["validation"]["issues"]
