#!/usr/bin/env python3
"""Convert a ChatGPT data export (conversations.json) into Label Studio tasks.

CHATGPT_CONVERSATION_IMPORT
--format paragraphs (default): messages go into data.dialogue for the
<Paragraphs layout="dialogue"> config, which supports text highlighting.
Region start/end in the export are indexes into data.dialogue.
--format chat: messages go into `predictions` as `chatmessage` results for the
<Chat> config (click-to-select whole messages only).

Usage:
  python3 chatgpt_to_labelstudio.py conversations.json --list
  python3 chatgpt_to_labelstudio.py conversations.json tasks.json --id https://chatgpt.com/c/<id> [--participant P01]
  python3 chatgpt_to_labelstudio.py conversations.json tasks.json [--participant P01] [--only TITLE_SUBSTRING] [--format paragraphs|chat]
"""
import argparse
import datetime
import json

KEEP_ROLES = {"user", "assistant"}
MODEL_VERSION = "chatgpt-import-v1"


def conv_id(conv):
    return conv.get("conversation_id") or conv.get("id")


def message_text(msg):
    content = msg.get("content") or {}
    if content.get("content_type") not in (None, "text", "multimodal_text"):
        return ""
    parts = []
    for p in content.get("parts") or []:
        if isinstance(p, str):
            parts.append(p)
        elif isinstance(p, dict) and p.get("content_type") == "image_asset_pointer":
            parts.append("[image]")
    return "\n".join(parts).strip()


def active_branch(conv):
    """Walk from current_node to the root so edited/regenerated branches are dropped."""
    mapping = conv["mapping"]
    node_id = conv.get("current_node")
    chain = []
    while node_id:
        node = mapping[node_id]
        chain.append(node)
        node_id = node.get("parent")
    return list(reversed(chain))


SPEAKER_NAMES = {"user": "You", "assistant": "ChatGPT"}


def convert(conv, participant=None, fmt="paragraphs"):
    messages, results = [], []
    last_ms = 0
    for node in active_branch(conv):
        msg = node.get("message")
        if not msg:
            continue
        role = (msg.get("author") or {}).get("role")
        if role not in KEEP_ROLES:
            continue
        if (msg.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
            continue
        text = message_text(msg)
        if not text:
            continue
        ms = int((msg.get("create_time") or conv.get("create_time") or 0) * 1000)
        ms = max(ms, last_ms + 1)  # createdAt must be unique and increasing
        last_ms = ms
        idx = len(messages)
        messages.append({"message_id": msg["id"], "index": idx, "role": role, "createdAt": ms,
                         "author": SPEAKER_NAMES[role], "text": text})
        results.append({
            "id": f"m{idx}",
            "type": "chatmessage",
            "from_name": "chat",
            "to_name": "chat",
            "value": {"chatmessage": {"role": role, "content": text, "createdAt": ms}},
        })
    data = {
        "max": len(messages),
        "conversation_id": conv.get("conversation_id") or conv.get("id"),
        "title": conv.get("title"),
        "participant_id": participant,
        "codebook_version": "typology-v1",
    }
    if fmt == "chat":
        data["chat"] = []
        data["messages_meta"] = [{k: m[k] for k in ("message_id", "index", "role", "createdAt")} for m in messages]
        return {"data": data, "predictions": [{"model_version": MODEL_VERSION, "result": results}]}
    data["dialogue"] = messages
    return {"data": data}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export_json")
    ap.add_argument("out_json", nargs="?")
    ap.add_argument("--list", action="store_true", help="print each conversation's ID, date and title, then exit")
    ap.add_argument("--id", action="append", help="conversation ID or chatgpt.com/c/... link; repeat for several")
    ap.add_argument("--participant")
    ap.add_argument("--only", help="keep conversations whose title contains this text")
    ap.add_argument("--format", choices=["paragraphs", "chat"], default="paragraphs")
    a = ap.parse_args()
    with open(a.export_json, encoding="utf-8") as f:
        convs = json.load(f)
    if isinstance(convs, dict):
        convs = [convs]
    if a.list:
        for c in sorted(convs, key=lambda c: c.get("create_time") or 0, reverse=True):
            day = datetime.datetime.fromtimestamp(c.get("create_time") or 0).strftime("%Y-%m-%d")
            print(f"{conv_id(c)}  {day}  {c.get('title') or '(untitled)'}")
        return
    if not a.out_json:
        ap.error("out_json is required unless --list is used")
    if a.id:
        wanted = {i.rstrip("/").split("/")[-1].split("?")[0] for i in a.id}
        convs = [c for c in convs if conv_id(c) in wanted]
        missing = wanted - {conv_id(c) for c in convs}
        if missing:
            ap.error("not found in export: " + ", ".join(sorted(missing)))
    if a.only:
        convs = [c for c in convs if a.only.lower() in (c.get("title") or "").lower()]
    tasks = [t for t in (convert(c, a.participant, a.format) for c in convs) if t["data"]["max"] >= 2]
    with open(a.out_json, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(tasks)} task(s) to {a.out_json}")


if __name__ == "__main__":
    main()
