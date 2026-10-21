#!/usr/bin/env python3
"""Check a Label Studio task file against labeling_config.xml before importing.

Usage: python3 validate_tasks.py tasks.json
"""
import json
import sys

ROLE_AUTHOR = {"user": "You", "assistant": "ChatGPT"}


def check(tasks):
    problems = []
    if not isinstance(tasks, list):
        return ["file must be a JSON list of tasks: [ {...}, {...} ]"]
    for t_i, task in enumerate(tasks):
        where = f"task {t_i}"
        if not isinstance(task, dict) or not isinstance(task.get("data"), dict):
            problems.append(f"{where}: must look like {{\"data\": {{...}}}}")
            continue
        for key in ("annotations", "predictions", "result"):
            if key in task or key in task["data"]:
                problems.append(f"{where}: has '{key}'. This looks like an export, not a conversation")
        dialogue = task["data"].get("dialogue")
        if not isinstance(dialogue, list) or not dialogue:
            problems.append(f"{where}: data.dialogue must be a non-empty list of messages")
            continue
        ids = set()
        for m_i, msg in enumerate(dialogue):
            w = f"{where} message {m_i}"
            if not isinstance(msg, dict):
                problems.append(f"{w}: must be an object")
                continue
            if not isinstance(msg.get("text"), str) or not msg["text"].strip():
                problems.append(f"{w}: 'text' must be a non-empty string")
            author = msg.get("author")
            if author not in ROLE_AUTHOR.values():
                problems.append(f"{w}: 'author' is {author!r}; must be exactly \"You\" or \"ChatGPT\" for the chat styling")
            role = msg.get("role")
            if role in ROLE_AUTHOR and author != ROLE_AUTHOR[role]:
                problems.append(f"{w}: role {role!r} does not match author {author!r}")
            if msg.get("index") not in (None, m_i):
                problems.append(f"{w}: 'index' is {msg.get('index')}, expected {m_i}")
            mid = msg.get("message_id")
            if mid is None:
                problems.append(f"{w}: missing 'message_id' (needed to link highlights back to ChatGPT messages)")
            elif mid in ids:
                problems.append(f"{w}: duplicate message_id {mid!r}")
            ids.add(mid)
    return problems


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        tasks = json.load(f)
    problems = check(tasks)
    if problems:
        print(f"{len(problems)} problem(s):")
        print("\n".join("  - " + p for p in problems))
        sys.exit(1)
    print(f"OK: {len(tasks)} task(s), {sum(len(t['data']['dialogue']) for t in tasks)} messages, ready to import")
