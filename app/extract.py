"""Retrieve a public ChatGPT share page and extract its messages.

Order of attempts:
  1. Structured data embedded in the page (__NEXT_DATA__ JSON, or React Router
     turbo-stream). Gives the original message text, IDs and order.
  2. Optional: render the page in headless Chromium and read the messages from
     the page (SHARE_LINK_BROWSER_FALLBACK=1). Text is the rendered text, so
     Markdown formatting is approximated; the participant is warned.

Only https://chatgpt.com/share/<uuid> (and chat.openai.com) is ever fetched,
redirects included. The extractor never logs in or works around access controls.
"""
import html as htmllib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import turbo_stream
from .settings import settings

ALLOWED_HOSTS = {"chatgpt.com", "chat.openai.com"}
SHARE_PATH = re.compile(r"^/share/(?:e/)?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/?$")
MAX_BYTES = 25 * 1024 * 1024
MAX_REDIRECTS = 5
USER_AGENT = "TypologyStudyImporter/1.0 (research study; imports a link the participant shared)"
KEEP_ROLES = {"user", "assistant"}
TEXT_TYPES = {"text", "multimodal_text"}
EXTRACT_JS = (Path(__file__).resolve().parent / "static" / "extract_messages.js").read_text()


class ExtractionError(Exception):
    """code is machine-readable; message is safe to show the participant (no content)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Extracted:
    share_id: str
    title: str | None
    messages: list            # [{"source_id", "role", "text", "create_time"}]
    method: str               # "structured" | "rendered_page"
    warnings: list = field(default_factory=list)   # [{"code", "message"}]

    @property
    def complete(self):
        return not any(w["code"] in UNCERTAIN_CODES for w in self.warnings)


UNCERTAIN_CODES = {"broken_chain", "rendered_text", "unsupported_content", "one_sided"}


# ---------- URL handling ----------

def validate_share_url(url):
    """Return (canonical_url, share_id) or raise ExtractionError."""
    if not isinstance(url, str) or len(url) > 300:
        raise ExtractionError("invalid_url", "Please paste a ChatGPT share link, like https://chatgpt.com/share/…")
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in ALLOWED_HOSTS or parts.port not in (None, 443) or parts.username or parts.password:
        raise ExtractionError("invalid_url", "Only ChatGPT share links (https://chatgpt.com/share/…) can be imported.")
    m = SHARE_PATH.match(parts.path)
    if not m:
        raise ExtractionError("invalid_url", "That link isn't a ChatGPT share link. It should look like https://chatgpt.com/share/…")
    share_id = m.group(1).lower()
    return f"https://chatgpt.com/share/{share_id}", share_id


def _fetch_target(canonical_url):
    if settings.is_test and settings.test_share_origin:
        return settings.test_share_origin.rstrip("/") + urlsplit(canonical_url).path
    return canonical_url


def _redirect_allowed(url):
    parts = urlsplit(url)
    if settings.is_test and settings.test_share_origin:
        t = urlsplit(settings.test_share_origin)
        return (parts.scheme, parts.netloc) == (t.scheme, t.netloc)
    return parts.scheme == "https" and (parts.hostname or "").lower() in ALLOWED_HOSTS and parts.port in (None, 443)


async def fetch_page(canonical_url, transport=None):
    url = _fetch_target(canonical_url)
    async with httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(20.0), transport=transport,
                                 headers={"User-Agent": USER_AGENT, "Accept": "text/html"}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                async with client.stream("GET", url) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        nxt = str(resp.url.join(resp.headers.get("location", "")))
                        if not _redirect_allowed(nxt):
                            raise ExtractionError("blocked_redirect", "That link redirected somewhere we can't import from.")
                        url = nxt
                        continue
                    if resp.status_code in (404, 410):
                        raise ExtractionError("not_found", "That conversation wasn't found. It may have been deleted or the link turned off.")
                    if resp.status_code in (401, 403, 429):
                        raise ExtractionError("inaccessible", "ChatGPT didn't let us open that link right now.")
                    if resp.status_code >= 400:
                        raise ExtractionError("unavailable", "ChatGPT didn't respond normally. Please try again later.")
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise ExtractionError("too_large", "That page is too large to import.")
                    return body.decode(resp.encoding or "utf-8", errors="replace")
            except httpx.HTTPError as e:
                raise ExtractionError("unavailable", "We couldn't reach ChatGPT. Please try again later.") from e
        raise ExtractionError("blocked_redirect", "That link redirected too many times.")


# ---------- structured data ----------

_NEXT_DATA = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_ENQUEUE = re.compile(r'streamController\.enqueue\(("(?:[^"\\]|\\.)*")\)', re.S)
_JSON_SCRIPT = re.compile(r'<script[^>]+type="application/json"[^>]*>(.*?)</script>', re.S)


def _candidate_roots(page):
    m = _NEXT_DATA.search(page)
    if m:
        try:
            yield json.loads(htmllib.unescape(m.group(1)))
        except ValueError:
            pass
    chunks = []
    for lit in _ENQUEUE.findall(page):
        try:
            chunks.append(json.loads(lit))
        except ValueError:
            continue
    if chunks:
        yield from turbo_stream.decode_stream("".join(chunks))
    for body in _JSON_SCRIPT.findall(page):
        try:
            yield json.loads(htmllib.unescape(body))
        except ValueError:
            continue


def _is_conversation(obj):
    if not isinstance(obj, dict):
        return False
    mapping = obj.get("mapping")
    if isinstance(mapping, dict) and mapping and all(isinstance(v, dict) for v in mapping.values()):
        return any(isinstance(v.get("message"), dict) for v in mapping.values())
    lin = obj.get("linear_conversation")
    return isinstance(lin, list) and any(isinstance(n, dict) and isinstance(n.get("message"), dict) for n in lin)


def find_conversation(root, limit=200000):
    stack, seen, steps = [root], set(), 0
    while stack and steps < limit:
        obj = stack.pop()
        steps += 1
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        if _is_conversation(obj):
            return obj
        if isinstance(obj, dict):
            stack.extend(obj.values())
        elif isinstance(obj, list):
            stack.extend(obj)
    return None


def _message_text(msg, warnings):
    content = msg.get("content") or {}
    ctype = content.get("content_type")
    if ctype not in TEXT_TYPES:
        return None, ctype
    parts = []
    for p in content.get("parts") or []:
        if isinstance(p, str):
            parts.append(p)
        elif isinstance(p, dict):
            if "image" in str(p.get("content_type", "")):
                _warn(warnings, "images_omitted", "Images in this conversation aren't included; a placeholder marks where they were.")
                parts.append("[image]")
            else:
                _warn(warnings, "unsupported_content", "Some content (such as files or tools) couldn't be shown.")
    return "\n".join(parts), ctype


def _warn(warnings, code, message):
    if not any(w["code"] == code for w in warnings):
        warnings.append({"code": code, "message": message})


def _ordered_nodes(conv, warnings):
    mapping = conv.get("mapping") if isinstance(conv.get("mapping"), dict) else {}
    current = conv.get("current_node")
    if mapping and current in mapping:
        chain, node_id, guard = [], current, 0
        while node_id and guard < 100000:
            node = mapping.get(node_id)
            if node is None:
                _warn(warnings, "broken_chain", "Part of the conversation may be missing.")
                break
            chain.append(node)
            node_id = node.get("parent")
            guard += 1
        chain.reverse()
        in_chain = sum(1 for n in chain if isinstance(n.get("message"), dict))
        if in_chain < sum(1 for n in mapping.values() if isinstance(n.get("message"), dict)):
            _warn(warnings, "branches_omitted", "Only the final version of edited or regenerated messages is included.")
        return chain
    if isinstance(conv.get("linear_conversation"), list):
        return conv["linear_conversation"]
    # No current_node: follow the longest parent chain.
    leaves = [n for n in mapping.values() if not n.get("children")]
    best = []
    for leaf in leaves:
        chain, node = [], leaf
        while node is not None and len(chain) < 100000:
            chain.append(node)
            node = mapping.get(node.get("parent")) if node.get("parent") else None
        if len(chain) > len(best):
            best = chain
    _warn(warnings, "branches_omitted", "Only the final version of edited or regenerated messages is included.")
    return list(reversed(best))


def messages_from_conversation(conv):
    warnings, messages = [], []
    for node in _ordered_nodes(conv, warnings):
        msg = node.get("message") if isinstance(node, dict) else None
        if not isinstance(msg, dict):
            continue
        role = (msg.get("author") or {}).get("role")
        meta = msg.get("metadata") or {}
        if role not in KEEP_ROLES or meta.get("is_visually_hidden_from_conversation"):
            continue
        if role == "assistant" and msg.get("recipient", "all") != "all":
            continue  # tool call, not shown to the user
        text, ctype = _message_text(msg, warnings)
        if text is None:
            if role == "user" or ctype not in (None, "thoughts", "reasoning_recap", "model_editable_context", "code", "execution_output"):
                _warn(warnings, "unsupported_content", "Some content (such as files or tools) couldn't be shown.")
            continue
        if not text.strip():
            continue
        messages.append({"source_id": str(msg.get("id") or node.get("id") or ""), "role": role, "text": text,
                         "create_time": msg.get("create_time")})
    return messages, warnings


def parse_structured(page, share_id):
    for root in _candidate_roots(page):
        conv = find_conversation(root)
        if conv is not None:
            messages, warnings = messages_from_conversation(conv)
            title = conv.get("title") if isinstance(conv.get("title"), str) else None
            return Extracted(share_id, title, messages, "structured", warnings)
    return None


# ---------- browser fallback ----------

async def render_and_extract(canonical_url, share_id):
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise ExtractionError("extraction_failed", "We couldn't read that conversation.") from e
    allowed_resource_hosts = ALLOWED_HOSTS | {"cdn.oaistatic.com"}
    test_origin = urlsplit(settings.test_share_origin) if settings.is_test and settings.test_share_origin else None

    async def guard(route):
        parts = urlsplit(route.request.url)
        ok = (parts.scheme == "https" and (parts.hostname or "") in allowed_resource_hosts) or \
             (test_origin is not None and (parts.scheme, parts.netloc) == (test_origin.scheme, test_origin.netloc))
        await (route.continue_() if ok else route.abort())

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            ctx = await browser.new_context(user_agent=USER_AGENT, java_script_enabled=True, accept_downloads=False)
            await ctx.route("**/*", guard)
            page = await ctx.new_page()
            resp = await page.goto(_fetch_target(canonical_url), wait_until="networkidle", timeout=45000)
            if resp is not None and resp.status in (404, 410):
                raise ExtractionError("not_found", "That conversation wasn't found. It may have been deleted or the link turned off.")
            dialogue = await page.evaluate(EXTRACT_JS)
            title = await page.title()
        except ExtractionError:
            raise
        except Exception as e:
            raise ExtractionError("extraction_failed", "We couldn't read that conversation.") from e
        finally:
            await browser.close()
    messages = [{"source_id": str(m.get("message_id") or ""), "role": m["role"], "text": m["text"], "create_time": None}
                for m in dialogue or [] if m.get("role") in KEEP_ROLES]
    title = re.sub(r"\s*[-|]\s*ChatGPT\s*$", "", title or "") or None
    return Extracted(share_id, title, messages, "rendered_page",
                     [{"code": "rendered_text", "message": "We read this conversation from the displayed page, so some formatting may differ from the original. Please check it carefully."}])


# ---------- entry point ----------

async def extract(url, transport=None):
    canonical, share_id = validate_share_url(url)
    page = await fetch_page(canonical, transport=transport)
    result = parse_structured(page, share_id)
    if result is None and settings.browser_fallback:
        result = await render_and_extract(canonical, share_id)
    if result is None:
        raise ExtractionError("extraction_failed", "We couldn't find the conversation on that page. ChatGPT may have changed its page format.")
    if not result.messages:
        raise ExtractionError("empty", "That conversation has no messages we can import.")
    roles = {m["role"] for m in result.messages}
    if roles != {"user", "assistant"}:
        _warn(result.warnings, "one_sided", "We only found messages from one side of the conversation. Some messages may be missing.")
    return result
