"""TYPOLOGY_ANNOTATION_VALIDATION: check a Label Studio result before it is saved.

Every answer is optional, so only structure and consistency are checked:
highlights must point inside one message and match its text, answers must use
the XML's options, and a question that belongs to the "my text" panel can't be
attached to an AI highlight (or the reverse).
"""
import re

from . import labeling_config

MAX_RESULTS = 5000
MAX_TEXT_ANSWER = 20000
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ResultError(ValueError):
    pass


def utf16_len(text):
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def utf16_to_index(text, offset):
    """Label Studio counts JavaScript (UTF-16) units; convert to a Python string index."""
    units = 0
    for i, ch in enumerate(text):
        if units == offset:
            return i
        if units > offset:
            return None  # offset falls inside a surrogate pair
        units += 2 if ord(ch) > 0xFFFF else 1
    return len(text) if units == offset else None


def span_text(text, start_offset, end_offset):
    a, b = utf16_to_index(text, start_offset), utf16_to_index(text, end_offset)
    if a is None or b is None or a > b:
        return None
    return text[a:b]


def validate(result, dialogue):
    cfg = labeling_config.load()
    if not isinstance(result, list) or len(result) > MAX_RESULTS:
        raise ResultError("result must be a list")
    regions = {}
    for r in result:
        if not isinstance(r, dict) or r.get("from_name") != cfg.span_control:
            continue
        rid, v = r.get("id"), r.get("value") or {}
        if not isinstance(rid, str) or not _ID.match(rid) or rid in regions:
            raise ResultError("highlight has an invalid id")
        if r.get("type") != "paragraphlabels" or r.get("to_name") != cfg.object_name:
            raise ResultError("unexpected highlight type")
        if not (str(v.get("start", "")).isdigit() and str(v.get("end", "")).isdigit()):
            raise ResultError("highlight has no message position")
        start, end = int(v["start"]), int(v["end"])
        if start != end:
            raise ResultError("a highlight must stay within one message")
        if not 0 <= start < len(dialogue):
            raise ResultError("highlight points outside the conversation")
        msg = dialogue[start]
        so, eo = v.get("startOffset"), v.get("endOffset")
        if not (isinstance(so, int) and isinstance(eo, int) and 0 <= so < eo <= utf16_len(msg["text"])):
            raise ResultError("highlight has invalid character offsets")
        if "text" in v and span_text(msg["text"], so, eo) != v["text"]:
            raise ResultError("highlighted text doesn't match the conversation")
        labels = v.get("paragraphlabels")
        if not (isinstance(labels, list) and len(labels) == 1 and labels[0] in (cfg.user_label, cfg.assistant_label)):
            raise ResultError("highlight needs exactly one label")
        expected = cfg.user_label if msg["role"] == "user" else cfg.assistant_label
        if labels[0] != expected:
            raise ResultError(f"a highlight in {'your' if msg['role'] == 'user' else 'the AI' + chr(39) + 's'} message must use '{expected}'")
        regions[rid] = {"start": start, "label": labels[0]}

    for r in result:
        if not isinstance(r, dict):
            raise ResultError("unexpected result entry")
        name = r.get("from_name")
        if name == cfg.span_control:
            continue
        ctrl = cfg.controls.get(name)
        if ctrl is None or r.get("type") != ctrl["type"] or r.get("to_name") != cfg.object_name:
            raise ResultError(f"unknown question '{str(name)[:40]}'")
        v = r.get("value") or {}
        if ctrl["per_region"]:
            region = regions.get(r.get("id"))
            if region is None:
                raise ResultError("an answer isn't attached to a highlight")
            if ctrl["label"] and ctrl["label"] != region["label"]:
                raise ResultError(f"'{name}' can't be answered for a '{region['label']}' highlight")
            if str(v.get("start")) != str(region["start"]):
                raise ResultError("an answer points at a different message than its highlight")
        elif "start" in v or "startOffset" in v:
            raise ResultError(f"'{name}' is a conversation-level question")
        if ctrl["type"] == "choices":
            ch = v.get("choices")
            if not (isinstance(ch, list) and ch and all(c in ctrl["choices"] for c in ch)) or (not ctrl["multiple"] and len(ch) > 1):
                raise ResultError(f"invalid option for '{name}'")
        else:
            txt = v.get("text")
            if not (isinstance(txt, list) and txt and all(isinstance(t, str) and 0 < len(t) <= MAX_TEXT_ANSWER for t in txt)):
                raise ResultError(f"invalid written answer for '{name}'")
            if ctrl["max_submissions"] and len(txt) > ctrl["max_submissions"]:
                raise ResultError(f"too many answers for '{name}'")
    return len(regions)
