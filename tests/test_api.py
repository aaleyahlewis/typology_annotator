import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "fake_conversations.json"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPOLOGY_DB", str(tmp_path / "t.sqlite3"))
    monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")
    from app import db, main
    db.init()
    db.add_participant("P01", main.hash_code("code-1"))
    db.add_participant("P02", main.hash_code("code-2"))
    with TestClient(main.app) as c:
        yield c


def login(c, sid, code):
    return c.post("/api/login", json={"study_id": sid, "code": code})


def conversation():
    # Same shape the browser converter (chatgpt_export.js) sends.
    out = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "chatgpt_to_labelstudio.py"), str(FIXTURE), "/dev/stdout"],
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(out[: out.rindex("]") + 1])[0]["data"]
    return {"conversation_id": data["conversation_id"], "title": data["title"], "dialogue": data["dialogue"]}


def test_login_required_and_wrong_code(client):
    assert client.get("/api/tasks").status_code == 401
    assert login(client, "P01", "wrong").status_code == 401
    assert login(client, "P01", "code-1").status_code == 200


def test_json_required_for_writes(client):
    login(client, "P01", "code-1")
    r = client.post("/api/tasks", data="conversation=x", headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415


def test_create_annotate_export(client):
    login(client, "P01", "code-1")
    tid = client.post("/api/tasks", json={"conversation": conversation(), "source": "export_file"}).json()["id"]
    task = client.get(f"/api/tasks/{tid}").json()
    assert [m["author"] for m in task["data"]["dialogue"][:2]] == ["You", "ChatGPT"]
    text = task["data"]["dialogue"][0]["text"]
    start = text.index("I come across as blunt")
    span = {"start": "0", "end": "0", "startOffset": start, "endOffset": start + 22, "text": "I come across as blunt"}
    result = [
        {"id": "r1", "from_name": "passage_type", "to_name": "dialogue", "type": "paragraphlabels", "value": {**span, "paragraphlabels": ["My words"]}},
        {"id": "r1", "from_name": "user_why", "to_name": "dialogue", "type": "textarea", "value": {**span, "text": ["Expectations at work"]}},
        {"id": "r2", "from_name": "passage_type", "to_name": "dialogue", "type": "paragraphlabels",
         "value": {"start": "0", "end": "0", "startOffset": start + 2, "endOffset": start + 22, "text": "come across as blunt", "paragraphlabels": ["My words"]}},
        {"id": "c1", "from_name": "conversation_additional_context", "to_name": "dialogue", "type": "textarea", "value": {"text": ["Context"]}},
    ]
    assert client.put(f"/api/tasks/{tid}/annotation", json={"result": result, "submitted": True}).status_code == 200
    exp = client.get("/admin/export", headers={"Authorization": "Bearer admin-secret"}).json()
    conv = exp["conversations"][0]
    assert conv["participant_id"] == "P01" and conv["submitted"] is True
    assert conv["conversation_context"] == {"conversation_additional_context": ["Context"]}
    spans = {s["annotation_id"]: s for s in conv["spans"]}
    assert spans["r1"]["message_id"] == "msg-1" and spans["r1"]["role"] == "user"
    assert spans["r1"]["answers"] == {"user_why": ["Expectations at work"]}
    assert text[spans["r1"]["start_char"]:spans["r1"]["end_char"]] == "I come across as blunt"
    assert spans["r2"]["answers"] == {}  # overlapping span, no answers


def test_participants_are_isolated(client):
    login(client, "P01", "code-1")
    tid = client.post("/api/tasks", json={"conversation": conversation()}).json()["id"]
    client.post("/api/logout", json={})
    login(client, "P02", "code-2")
    assert client.get(f"/api/tasks/{tid}").status_code == 404
    assert client.put(f"/api/tasks/{tid}/annotation", json={"result": []}).status_code == 404
    assert client.get("/api/tasks").json() == []


def test_rejects_bad_input(client):
    login(client, "P01", "code-1")
    assert client.post("/api/tasks", json={"conversation": {"dialogue": [{"role": "user", "text": "hi"}]}}).status_code == 400
    tid = client.post("/api/tasks", json={"conversation": conversation()}).json()["id"]
    bad = [{"id": "x", "type": "paragraphlabels", "from_name": "passage_type", "value": {"start": "99", "end": "99"}}]
    assert client.put(f"/api/tasks/{tid}/annotation", json={"result": bad}).status_code == 400


def test_export_needs_admin_token(client):
    assert client.get("/admin/export").status_code == 401
    assert client.get("/admin/export", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_share_link_off_by_default_and_url_checked(client, monkeypatch):
    login(client, "P01", "code-1")
    assert client.post("/api/share-link", json={"url": "https://chatgpt.com/share/abc"}).status_code == 503
    monkeypatch.setenv("ENABLE_SHARE_LINK_IMPORT", "1")
    r = client.post("/api/share-link", json={"url": "http://169.254.169.254/latest"})
    assert r.status_code == 422 and "share link" in r.json()["detail"]


def test_utf16_offsets_with_emoji():
    from app.export import utf16_to_codepoint
    text = "hi 🙂 there"
    assert text[utf16_to_codepoint(text, 6):] == "there"
