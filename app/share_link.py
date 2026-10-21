"""Optional: read a public ChatGPT share link on the server with a headless browser.

Off by default (ENABLE_SHARE_LINK_IMPORT=1 turns it on). ChatGPT may block automated
requests and changes its page structure without notice, and automated access may
conflict with OpenAI's terms, so the export-file upload is always offered as the
fallback. Requires `pip install playwright` and a Chromium install on the server.
"""
import os
import re
from pathlib import Path

SHARE_URL = re.compile(r"^https://(chatgpt\.com|chat\.openai\.com)/share/[0-9a-fA-F-]{20,64}/?$")
EXTRACT_JS = (Path(__file__).resolve().parent / "static" / "extract_messages.js").read_text()


class ShareLinkError(Exception):
    pass


def enabled():
    return os.environ.get("ENABLE_SHARE_LINK_IMPORT") == "1"


async def fetch(url):
    # Only ever open ChatGPT share URLs, so the server cannot be pointed at other sites.
    if not SHARE_URL.match(url.strip()):
        raise ShareLinkError("That doesn't look like a ChatGPT share link (https://chatgpt.com/share/...).")
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise ShareLinkError("Link import isn't installed on this server.") from e
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(url.strip(), wait_until="networkidle", timeout=45000)
            dialogue = await page.evaluate(EXTRACT_JS)
            title = await page.title()
        except Exception as e:
            raise ShareLinkError("We couldn't open that link.") from e
        finally:
            await browser.close()
    if not dialogue or len(dialogue) < 2:
        raise ShareLinkError("We couldn't find the messages on that page.")
    return {"conversation_id": url.rstrip("/").split("/")[-1],
            "title": re.sub(r"\s*[-|]\s*ChatGPT\s*$", "", title or ""), "dialogue": dialogue}
