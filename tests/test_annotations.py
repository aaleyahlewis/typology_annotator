import pytest

from app import annotations
from tests.helpers import fixtures as f

DIALOGUE = [{"message_id": f"m{i}", "index": i, "role": r, "text": t} for i, (r, t) in enumerate(f.UNICODE_TURNS)]


def utf16(text, sub):
    i = text.index(sub)
    return len(text[:i].encode("utf-16-le")) // 2, len(text[:i + len(sub)].encode("utf-16-le")) // 2


def span(rid, msg, sub, label=None):
    text = DIALOGUE[msg]["text"]
    so, eo = utf16(text, sub)
    label = label or ("my text" if DIALOGUE[msg]["role"] == "user" else "AI's text")
    return {"id": rid, "from_name": "passage_type", "to_name": "dialogue", "type": "paragraphlabels",
            "value": {"start": str(msg), "end": str(msg), "startOffset": so, "endOffset": eo, "text": sub, "paragraphlabels": [label]}}


def answer(rid, msg, name, kind, value, sub):
    base = span(rid, msg, sub)["value"]
    v = {k: base[k] for k in ("start", "end", "startOffset", "endOffset")}
    v["choices" if kind == "choices" else "text"] = value
    return {"id": rid, "from_name": name, "to_name": "dialogue", "type": kind, "value": v}


def test_valid_result_with_unicode_overlap_and_context():
    result = [
        span("a", 0, "neurodivergent 🙂 to get"),
        span("b", 0, "🙂 to get the AI"),                                   # overlapping
        answer("a", 0, "user_why", "textarea", ["Expectations"], "neurodivergent 🙂 to get"),
        answer("a", 0, "user_where", "choices", ["Professional / Workplace", "Community"], "neurodivergent 🙂 to get"),
        span("c", 1, "谢谢, شكرا 👩🏽‍🦯"),
        answer("c", 1, "ai_role", "choices", ["Supporting", "Undermining"], "谢谢, شكرا 👩🏽‍🦯"),
        {"id": "x", "from_name": "conversation_accessibility_context", "to_name": "dialogue", "type": "textarea", "value": {"text": ["ctx"]}},
    ]
    assert annotations.validate(result, DIALOGUE) == 3


def test_empty_result_is_valid():
    assert annotations.validate([], DIALOGUE) == 0


def test_span_text_converts_utf16():
    text = DIALOGUE[1]["text"]
    so, eo = utf16(text, "👩🏽‍🦯")
    assert annotations.span_text(text, so, eo) == "👩🏽‍🦯"
    assert annotations.span_text(text, so + 1, eo) is None   # inside a surrogate pair


@pytest.mark.parametrize("name,bad,msg", [
    ("cross-message", lambda r: r[0]["value"].update(end="1"), "within one message"),
    ("outside", lambda r: r[0]["value"].update(start="9", end="9"), "outside"),
    ("offsets", lambda r: r[0]["value"].update(endOffset=10**6), "offsets"),
    ("text mismatch", lambda r: r[0]["value"].update(text="something else"), "doesn't match"),
    ("label/role", lambda r: r[0]["value"].update(paragraphlabels=["AI's text"]), "must use 'my text'"),
    ("two labels", lambda r: r[0]["value"].update(paragraphlabels=["my text", "AI's text"]), "exactly one label"),
    ("bad id", lambda r: r[0].update(id="<script>"), "invalid id"),
])
def test_rejects_bad_highlights(name, bad, msg):
    r = [span("a", 0, "neurodivergent")]
    bad(r)
    with pytest.raises(annotations.ResultError, match=msg):
        annotations.validate(r, DIALOGUE)


def test_user_question_cannot_attach_to_ai_highlight():
    r = [span("c", 1, "Point one"), answer("c", 1, "user_why", "textarea", ["x"], "Point one")]
    with pytest.raises(annotations.ResultError, match="can't be answered"):
        annotations.validate(r, DIALOGUE)


@pytest.mark.parametrize("extra,msg", [
    ({"id": "zz", "from_name": "user_why", "to_name": "dialogue", "type": "textarea",
      "value": {"start": "0", "end": "0", "startOffset": 0, "endOffset": 1, "text": ["x"]}}, "isn't attached"),
    ({"id": "a", "from_name": "user_where", "to_name": "dialogue", "type": "choices",
      "value": {"start": "0", "end": "0", "choices": ["Mars"]}}, "invalid option"),
    ({"id": "a", "from_name": "not_in_xml", "to_name": "dialogue", "type": "textarea", "value": {"text": ["x"]}}, "unknown question"),
    ({"id": "a", "from_name": "conversation_additional_context", "to_name": "dialogue", "type": "textarea",
      "value": {"start": "0", "text": ["x"]}}, "conversation-level"),
    ({"id": "a", "from_name": "user_why", "to_name": "dialogue", "type": "textarea",
      "value": {"start": "0", "end": "0", "text": ["a", "b"]}}, "too many"),
    ({"id": "a", "from_name": "user_why", "to_name": "dialogue", "type": "textarea",
      "value": {"start": "2", "end": "2", "text": ["a"]}}, "different message"),
    ({"id": "a", "from_name": "user_why", "to_name": "dialogue", "type": "textarea",
      "value": {"start": "0", "end": "0", "text": ["x" * 20001]}}, "invalid written"),
])
def test_rejects_bad_answers(extra, msg):
    with pytest.raises(annotations.ResultError, match=msg):
        annotations.validate([span("a", 0, "neurodivergent"), extra], DIALOGUE)


def test_single_choice_question_rejects_two_options():
    r = [span("c", 1, "Point one"), answer("c", 1, "ai_followup_adjustment", "choices", ["Yes", "No"], "Point one")]
    with pytest.raises(annotations.ResultError, match="invalid option"):
        annotations.validate(r, DIALOGUE)
