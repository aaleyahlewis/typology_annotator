"""Turn extracted messages into the Label Studio task format and a canonical record.

Message text is never rewritten: no trimming, case changes, or Unicode
normalization. Only empty messages are dropped (during extraction).
"""
import hashlib
import re

from . import labeling_config

AUTHORS = {"user": "You", "assistant": "ChatGPT"}   # the chat styling in the XML relies on these names
MAX_MESSAGES = 3000
MAX_TEXT_CHARS = 400_000
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class NormalizationError(ValueError):
    """Message never includes conversation content."""


def message_ids(conversation_id, extracted_messages):
    """Source IDs when present, safe and unique; otherwise deterministic IDs scoped to the conversation."""
    sources = [m.get("source_id") or "" for m in extracted_messages]
    usable = all(_SAFE_ID.match(s) for s in sources) and len(set(sources)) == len(sources)
    if usable:
        return sources
    return [f"{conversation_id}_m{i:04d}" for i in range(len(extracted_messages))]


def build_dialogue(conversation_id, extracted_messages, keep_indexes=None):
    """keep_indexes: indexes into extracted_messages the participant approved (None = all)."""
    ids = message_ids(conversation_id, extracted_messages)
    keep = range(len(extracted_messages)) if keep_indexes is None else sorted(set(keep_indexes))
    dialogue = []
    for source_index in keep:
        if not 0 <= source_index < len(extracted_messages):
            raise NormalizationError("excluded message index out of range")
        m = extracted_messages[source_index]
        dialogue.append({
            "message_id": ids[source_index],
            "index": len(dialogue),
            "source_index": source_index,
            "role": m["role"],
            "author": AUTHORS[m["role"]],
            "text": m["text"],
        })
    return dialogue


def text_fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_dialogue(dialogue, extracted_messages=None):
    """Spec section 5 checks. Raises NormalizationError with a content-free message."""
    cfg = labeling_config.load()
    if not isinstance(dialogue, list) or not dialogue:
        raise NormalizationError("dialogue must be a non-empty list")
    if len(dialogue) > MAX_MESSAGES:
        raise NormalizationError("conversation has too many messages")
    seen = set()
    for i, m in enumerate(dialogue):
        if not isinstance(m, dict):
            raise NormalizationError(f"message {i} is not an object")
        if m.get("index") != i:
            raise NormalizationError(f"message {i} has index {m.get('index')!r}")
        if m.get("role") not in AUTHORS or m.get(cfg.name_key) != AUTHORS[m["role"]]:
            raise NormalizationError(f"message {i} has an invalid role/author")
        text = m.get(cfg.text_key)
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise NormalizationError(f"message {i} text is missing or too long")
        mid = m.get("message_id")
        if not isinstance(mid, str) or not _SAFE_ID.match(mid) or mid in seen:
            raise NormalizationError(f"message {i} has a missing or duplicate message_id")
        seen.add(mid)
        if extracted_messages is not None:
            src = extracted_messages[m["source_index"]]
            if src["text"] != text or src["role"] != m["role"]:
                raise NormalizationError(f"message {i} does not match the extracted message")
    if extracted_messages is not None:
        order = [m["source_index"] for m in dialogue]
        if order != sorted(order):
            raise NormalizationError("message order changed")


def task_data(conversation_id, participant_id, title, dialogue, schema_version):
    cfg = labeling_config.load()
    return {
        "conversation_id": conversation_id,
        "participant_id": participant_id,
        "title": title,
        "schema_version": schema_version,
        cfg.value_key: [{k: m[k] for k in ("message_id", "index", "role", cfg.name_key, cfg.text_key)} for m in dialogue],
    }
