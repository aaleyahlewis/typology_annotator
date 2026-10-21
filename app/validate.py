"""Server-side checks on conversations and annotation results before they are stored."""

ROLE_AUTHOR = {"user": "You", "assistant": "ChatGPT"}
MAX_MESSAGES = 2000
MAX_TEXT = 200_000
ALLOWED_RESULT_TYPES = {"paragraphlabels", "choices", "textarea"}


class ValidationError(ValueError):
    pass


def clean_conversation(data):
    """Return a normalised task data dict, or raise ValidationError."""
    if not isinstance(data, dict):
        raise ValidationError("conversation must be an object")
    dialogue = data.get("dialogue")
    if not isinstance(dialogue, list) or len(dialogue) < 2:
        raise ValidationError("conversation needs at least two messages")
    if len(dialogue) > MAX_MESSAGES:
        raise ValidationError("conversation is too long")
    cleaned, seen = [], set()
    for i, msg in enumerate(dialogue):
        if not isinstance(msg, dict):
            raise ValidationError(f"message {i} must be an object")
        role, text = msg.get("role"), msg.get("text")
        if role not in ROLE_AUTHOR:
            raise ValidationError(f"message {i}: role must be 'user' or 'assistant'")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise ValidationError(f"message {i}: text missing or too long")
        mid = str(msg.get("message_id") or f"msg-{i + 1}")
        if mid in seen:
            raise ValidationError(f"message {i}: duplicate message_id")
        seen.add(mid)
        # author is derived from role so the chat styling always matches
        cleaned.append({"message_id": mid, "index": i, "role": role, "author": ROLE_AUTHOR[role],
                        "text": text, "createdAt": msg.get("createdAt")})
    return {
        "max": len(cleaned),
        "conversation_id": str(data.get("conversation_id") or "")[:200] or None,
        "title": str(data.get("title") or "")[:300] or None,
        "codebook_version": "typology-v1",
        "dialogue": cleaned,
    }


def clean_result(result, dialogue_len):
    """TYPOLOGY_ANNOTATION_VALIDATION: every field is optional, so only structure is checked."""
    if not isinstance(result, list):
        raise ValidationError("result must be a list")
    if len(result) > 5000:
        raise ValidationError("too many results")
    for r in result:
        if not isinstance(r, dict) or r.get("type") not in ALLOWED_RESULT_TYPES:
            raise ValidationError("unexpected result entry")
        value = r.get("value") or {}
        for key in ("start", "end"):
            if key in value and not (str(value[key]).isdigit() and int(value[key]) < dialogue_len):
                raise ValidationError("highlight points outside the conversation")
    return result
