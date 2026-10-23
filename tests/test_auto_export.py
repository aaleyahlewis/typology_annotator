import json
import os
import stat

from app import export
from app.settings import settings
from tests.conftest import import_and_confirm, login
from tests.test_api import result_for


def files():
    folder = os.path.join(settings.export_dir, "conversations")
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def combined():
    with open(os.path.join(settings.export_dir, "typology_export.json"), encoding="utf-8") as f:
        return json.load(f)


def save(client, cid, result, version, submitted):
    r = client.put(f"/api/conversations/{cid}/annotation", json={"result": result, "version": version, "submitted": submitted})
    assert r.status_code == 200, r.text
    return r.json()["version"]


def test_draft_is_not_exported_submit_is(app_client):
    login(app_client)
    cid = import_and_confirm(app_client)
    v = save(app_client, cid, result_for(None), 0, False)
    assert files() == []
    save(app_client, cid, result_for(None), v, True)
    assert files() == [f"P01__{cid}.json"]
    path = os.path.join(settings.export_dir, "conversations", files()[0])
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    entry = json.load(open(path, encoding="utf-8"))
    assert entry["participant_id"] == "P01" and entry["validation"]["ok"] and len(entry["annotations"]) == 2
    assert combined()["summary"] == {"conversations": 1, "annotations": 2, "conversations_with_issues": []}


def test_update_after_submit_rewrites_export(app_client):
    login(app_client)
    cid = import_and_confirm(app_client)
    v = save(app_client, cid, result_for(None), 0, True)
    save(app_client, cid, result_for(None)[:2], v, True)       # participant removed the AI highlight
    assert combined()["summary"]["annotations"] == 1


def test_combined_file_has_every_participant(app_client):
    login(app_client)
    a = import_and_confirm(app_client)
    save(app_client, a, result_for(None), 0, True)
    app_client.post("/api/logout", json={})
    login(app_client, "P02", "code-two")
    b = import_and_confirm(app_client)
    save(app_client, b, [], 0, True)
    data = combined()
    assert {c["participant_id"] for c in data["conversations"]} == {"P01", "P02"}
    assert len(files()) == 2


def test_removing_conversation_removes_its_export(app_client):
    login(app_client)
    cid = import_and_confirm(app_client)
    save(app_client, cid, result_for(None), 0, True)
    app_client.delete(f"/api/conversations/{cid}", headers={"content-type": "application/json"})
    assert files() == [] and combined()["conversations"] == []


def test_export_failure_does_not_undo_save(app_client, monkeypatch, fake_ls):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(export, "_write_json", boom)
    login(app_client)
    cid = import_and_confirm(app_client)
    r = app_client.put(f"/api/conversations/{cid}/annotation", json={"result": result_for(None), "version": 0, "submitted": True})
    assert r.status_code == 200 and len(fake_ls.annotations) == 1
    assert app_client.get(f"/api/conversations/{cid}").json()["submitted"]


def test_can_be_turned_off(app_client):
    object.__setattr__(settings, "export_dir", "")
    login(app_client)
    cid = import_and_confirm(app_client)
    save(app_client, cid, result_for(None), 0, True)   # no error, nothing written
