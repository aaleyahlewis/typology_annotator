"""Synthetic ChatGPT share pages in the formats the extractor understands.

These mirror ChatGPT's page structure as of the time of writing; they are not
copies of real pages (chatgpt.com wasn't reachable from the build environment).
"""
import json
import uuid

SHARE_ID = "6729a1b2-0000-4000-8000-123456789abc"
SHARE_URL = f"https://chatgpt.com/share/{SHARE_ID}"

UNICODE_TURNS = [
    ("user", "I had to play the role of not being neurodivergent 🙂 to get the AI to help.\n\nCan you make my email less blunt?"),
    ("assistant", "Sure — here's a softer version:\n\n- Point one\n- Point two\n\n«Cordialement», 谢谢, شكرا 👩🏽‍🦯"),
    ("user", "That changed my meaning. Keep \"I can't process things on the spot\" — it's important."),
    ("assistant", "Understood. Here's a version that keeps your reason in your words."),
]


def conversation(turns=UNICODE_TURNS, title="Email to my manager (SYNTHETIC)", with_branch=True, hidden_system=True):
    mapping = {"root": {"id": "root", "message": None, "parent": None, "children": []}}
    parent = "root"
    if hidden_system:
        mapping["sys"] = {"id": "sys", "parent": "root", "children": [], "message": {
            "id": "sys", "author": {"role": "system"}, "content": {"content_type": "text", "parts": [""]},
            "metadata": {"is_visually_hidden_from_conversation": True}}}
        mapping["root"]["children"].append("sys")
        parent = "sys"
    for i, (role, text) in enumerate(turns):
        nid = str(uuid.UUID(int=i + 1))
        mapping[parent]["children"].append(nid)
        mapping[nid] = {"id": nid, "parent": parent, "children": [], "message": {
            "id": nid, "author": {"role": role}, "recipient": "all", "create_time": 1760000000 + i,
            "content": {"content_type": "text", "parts": [text]}, "metadata": {}}}
        if with_branch and i == 1:
            alt = str(uuid.UUID(int=999))
            mapping[parent]["children"].append(alt)
            mapping[alt] = {"id": alt, "parent": parent, "children": [], "message": {
                "id": alt, "author": {"role": "assistant"}, "content": {"content_type": "text", "parts": ["regenerated draft"]}}}
        parent = nid
    return {"title": title, "current_node": parent, "mapping": mapping, "conversation_id": SHARE_ID}


def next_data_page(conv=None):
    conv = conv or conversation()
    data = json.dumps({"props": {"pageProps": {"serverResponse": {"type": "data", "data": conv}}}})
    return f'<!doctype html><html><head><title>{conv["title"]}</title></head><body><div id="__next"></div>' \
           f'<script id="__NEXT_DATA__" type="application/json">{data}</script></body></html>'


def turbo_encode(root):
    """Flatten a value the way React Router's turbo-stream does."""
    values, index = [], {}

    def flat(v):
        if v is None:
            return -5
        key = (type(v).__name__, json.dumps(v, sort_keys=True)) if not isinstance(v, (dict, list)) else None
        if key and key in index:
            return index[key]
        i = len(values)
        values.append(None)
        if key:
            index[key] = i
        if isinstance(v, dict):
            values[i] = {f"_{flat(k)}": flat(x) for k, x in v.items()}
        elif isinstance(v, list):
            values[i] = [flat(x) for x in v]
        else:
            values[i] = v
        return i

    flat(root)
    return json.dumps(values)


def turbo_page(conv=None, split=3):
    conv = conv or conversation()
    payload = turbo_encode({"loaderData": {"routes/share.$shareId.($action)": {"serverResponse": {"type": "data", "data": conv}}}}) + "\n"
    step = max(1, len(payload) // split)
    chunks = [payload[i:i + step] for i in range(0, len(payload), step)]
    scripts = "".join(f"<script>window.__reactRouterContext.streamController.enqueue({json.dumps(c)});</script>" for c in chunks)
    return f'<!doctype html><html><head><title>{conv["title"]} | ChatGPT</title></head><body>{scripts}</body></html>'


def rendered_page(turns=UNICODE_TURNS, title="Email to my manager (SYNTHETIC)"):
    """Page with no embedded data, only rendered messages (exercises the browser fallback)."""
    import html
    arts = "".join(
        f'<article data-testid="conversation-turn-{i}" data-turn="{r}"><h5 class="sr-only">{"You said:" if r == "user" else "ChatGPT said:"}</h5>'
        f'<div data-message-author-role="{r}" data-message-id="{uuid.UUID(int=i + 1)}"><div style="white-space:pre-wrap">{html.escape(t)}</div></div></article>'
        for i, (r, t) in enumerate(turns))
    return f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)} | ChatGPT</title></head><body><main>{arts}</main></body></html>'
