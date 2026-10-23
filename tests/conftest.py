import os
import tempfile

# Settings are read at import time, so set the test environment first.
os.environ.update({
    "APP_ENV": "test",
    "SESSION_SECRET": "t" * 48,
    "HTTPS_ONLY": "0",
    "LABEL_STUDIO_URL": "http://labelstudio.invalid",
    "LABEL_STUDIO_API_KEY": "legacy-test-token",
    "LABEL_STUDIO_PROJECT_ID": "1",
    "ADMIN_TOKEN": "admin-secret",
    "TYPOLOGY_DB": os.path.join(tempfile.mkdtemp(), "bootstrap.sqlite3"),
})

import asyncio  # noqa: E402
import copy  # noqa: E402
import itertools  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, extract, labeling_config, labelstudio, main, security  # noqa: E402
from app.settings import settings  # noqa: E402
from tests.helpers import fixtures  # noqa: E402


class FakeLabelStudio:
    """In-memory stand-in for the Label Studio API used by the app."""

    def __init__(self):
        self.tasks, self.annotations, self.fail = {}, {}, set()
        self.label_config = labeling_config.load().xml
        self._ids = itertools.count(1)

    def _maybe_fail(self, op):
        if op in self.fail:
            raise labelstudio.LabelStudioError(f"{op} failed", 500)

    async def get_project(self):
        return {"id": 1, "title": "Test", "label_config": self.label_config}

    async def set_label_config(self, xml):
        self.label_config = xml

    async def create_task(self, data):
        self._maybe_fail("create_task")
        tid = next(self._ids)
        self.tasks[tid] = {"id": tid, "data": copy.deepcopy(data)}
        return tid

    async def get_task(self, tid):
        self._maybe_fail("get_task")
        if tid not in self.tasks:
            raise labelstudio.LabelStudioError("missing", 404)
        return copy.deepcopy(self.tasks[tid])

    async def delete_task(self, tid):
        self.tasks.pop(tid, None)
        for aid in [a for a, v in self.annotations.items() if v["task"] == tid]:
            del self.annotations[aid]

    async def find_task_by_conversation_id(self, cid):
        return next((t for t, v in self.tasks.items() if v["data"].get("conversation_id") == cid), None)

    async def create_annotation(self, tid, result, lead_time=None):
        self._maybe_fail("save")
        aid = next(self._ids)
        self.annotations[aid] = {"id": aid, "task": tid, "result": copy.deepcopy(result)}
        return aid

    async def update_annotation(self, aid, result):
        self._maybe_fail("save")
        self.annotations[aid]["result"] = copy.deepcopy(result)

    async def get_annotation(self, aid):
        return copy.deepcopy(self.annotations[aid])


PAGES = {
    fixtures.SHARE_ID: fixtures.turbo_page(),
    "11111111-0000-4000-8000-000000000001": fixtures.next_data_page(),
    "22222222-0000-4000-8000-000000000002": "<html><body>no data here</body></html>",
}


@pytest.fixture()
def fake_ls(monkeypatch):
    fake = FakeLabelStudio()
    monkeypatch.setattr(labelstudio, "client", lambda: fake)
    main._project_checked.update(at=0.0, ok=False)
    return fake


@pytest.fixture()
def app_client(tmp_path, monkeypatch, fake_ls):
    object.__setattr__(settings, "db_path", str(tmp_path / "t.sqlite3"))
    object.__setattr__(settings, "export_dir", str(tmp_path / "exports"))

    async def fake_fetch(canonical, transport=None):
        share_id = canonical.rsplit("/", 1)[1]
        if share_id not in PAGES:
            raise extract.ExtractionError("not_found", "That conversation wasn't found.")
        return PAGES[share_id]

    monkeypatch.setattr(extract, "fetch_page", fake_fetch)
    with TestClient(main.app) as c:
        for sid, code in (("P01", "code-one"), ("P02", "code-two")):
            db.add_participant(sid, security.hash_code(code))
        yield c


def login(client, sid="P01", code="code-one"):
    r = client.post("/api/login", json={"study_id": sid, "code": code})
    assert r.status_code == 200, r.text
    return r


def wait_ready(client, iid, timeout=5.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        body = client.get(f"/api/imports/{iid}").json()
        if body["status"] != "processing":
            return body
        time.sleep(0.05)
    raise AssertionError("import never finished")


def import_and_confirm(client, share_id=fixtures.SHARE_ID, exclude=()):
    r = client.post("/api/conversations/import", json={"shared_url": f"https://chatgpt.com/share/{share_id}"})
    assert r.status_code == 202, r.text
    body = wait_ready(client, r.json()["import_id"])
    assert body["status"] == "ready", body
    r = client.post(f"/api/imports/{body['import_id']}/confirm", json={"exclude": list(exclude)})
    assert r.status_code == 200, r.text
    return r.json()["conversation_id"]
