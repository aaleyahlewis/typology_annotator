"""Server-side Label Studio API client. Participants never talk to Label Studio directly.

Supports both kinds of Label Studio API keys:
  * Personal Access Token (a JWT refresh token, Label Studio 1.15+): exchanged at
    /api/token/refresh/ for short-lived access tokens, sent as "Bearer".
  * Legacy token: sent as "Token <key>".
"""
import base64
import json
import time

import httpx

from .settings import settings


class LabelStudioError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

    @property
    def retryable(self):
        return self.status is None or self.status >= 500 or self.status == 429


def _jwt_exp(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, ValueError):
        return None


class LabelStudio:
    def __init__(self, url=None, api_key=None, project_id=None, transport=None):
        self.url = (url or settings.ls_url).rstrip("/")
        self.api_key = api_key or settings.ls_api_key
        self.project_id = project_id or settings.ls_project_id
        self._transport = transport
        self._access = None
        self._access_exp = 0

    def _client(self):
        return httpx.AsyncClient(base_url=self.url, timeout=httpx.Timeout(30.0), transport=self._transport,
                                 follow_redirects=False)

    async def _auth_header(self, client):
        if _jwt_exp(self.api_key) is None:
            return {"Authorization": f"Token {self.api_key}"}
        if not self._access or time.time() > self._access_exp - 30:
            r = await client.post("/api/token/refresh/", json={"refresh": self.api_key})
            if r.status_code != 200:
                raise LabelStudioError("Label Studio rejected the API key", r.status_code)
            self._access = r.json()["access"]
            self._access_exp = _jwt_exp(self._access) or time.time() + 60
        return {"Authorization": f"Bearer {self._access}"}

    async def _request(self, method, path, **kw):
        try:
            async with self._client() as client:
                headers = await self._auth_header(client)
                r = await client.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as e:
            raise LabelStudioError(f"Label Studio unreachable: {type(e).__name__}") from e
        if r.status_code >= 400:
            # Response bodies can echo task data; keep only the status in the error.
            raise LabelStudioError(f"Label Studio {method} {path.split('?')[0]} failed", r.status_code)
        return r.json() if r.content else None

    # ---- project ----
    async def get_project(self):
        return await self._request("GET", f"/api/projects/{self.project_id}/")

    async def set_label_config(self, xml):
        return await self._request("PATCH", f"/api/projects/{self.project_id}/", json={"label_config": xml})

    # ---- tasks ----
    async def create_task(self, data):
        task = await self._request("POST", "/api/tasks/", json={"project": self.project_id, "data": data})
        return task["id"]

    async def get_task(self, task_id):
        return await self._request("GET", f"/api/tasks/{int(task_id)}/")

    async def delete_task(self, task_id):
        try:
            await self._request("DELETE", f"/api/tasks/{int(task_id)}/")
        except LabelStudioError as e:
            if e.status != 404:
                raise

    async def iter_tasks(self, page_size=100):
        page = 1
        while True:
            body = await self._request("GET", "/api/tasks/", params={"project": self.project_id, "page": page,
                                                                     "page_size": page_size, "fields": "all"})
            tasks = body.get("tasks", []) if isinstance(body, dict) else (body or [])
            for t in tasks:
                yield t
            if len(tasks) < page_size:
                return
            page += 1

    async def find_task_by_conversation_id(self, conversation_id):
        async for t in self.iter_tasks():
            if (t.get("data") or {}).get("conversation_id") == conversation_id:
                return t["id"]
        return None

    # ---- annotations ----
    async def create_annotation(self, task_id, result, lead_time=None):
        body = {"result": result}
        if lead_time is not None:
            body["lead_time"] = lead_time
        ann = await self._request("POST", f"/api/tasks/{int(task_id)}/annotations/", json=body)
        return ann["id"]

    async def update_annotation(self, annotation_id, result):
        return await self._request("PATCH", f"/api/annotations/{int(annotation_id)}/", json={"result": result})

    async def get_annotation(self, annotation_id):
        return await self._request("GET", f"/api/annotations/{int(annotation_id)}/")

    async def list_annotations(self, task_id):
        return await self._request("GET", f"/api/tasks/{int(task_id)}/annotations/")


def client():
    return LabelStudio()
