"""TYPOLOGY_DATA_EXPORT: turn stored Label Studio results into research rows.

Label Studio stores each highlight as several results sharing one region id: a
`paragraphlabels` result (the highlight itself) plus one result per answered
question. Results without a region id are conversation-level answers.
Offsets from the editor count UTF-16 code units (JavaScript string indexes);
`start_char`/`end_char` are the same positions in Unicode code points (Python).
"""
import json

from . import db

SPAN_LABEL = "passage_type"


def utf16_to_codepoint(text, offset):
    units = 0
    for i, ch in enumerate(text):
        if units >= offset:
            return i
        units += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def _answer(r):
    v = r.get("value", {})
    if r["type"] == "choices":
        return v.get("choices", [])
    if r["type"] == "textarea":
        return v.get("text", [])
    return None


def build_export():
    conversations = []
    for row in db.export_all():
        data = json.loads(row["data_json"])
        dialogue = data["dialogue"]
        results = json.loads(row["result_json"]) if row["result_json"] else []
        spans, conversation_answers = {}, {}
        for r in results:
            rid = r.get("id")
            v = r.get("value", {})
            if r.get("from_name") == SPAN_LABEL:
                idx = int(v["start"])
                msg = dialogue[idx]
                spans.setdefault(rid, {}).update({
                    "annotation_id": rid,
                    "message_id": msg["message_id"],
                    "message_index": idx,
                    "role": msg["role"],
                    "label": (v.get("paragraphlabels") or [None])[0],
                    "selected_text": v.get("text"),
                    "start_offset_utf16": v.get("startOffset"),
                    "end_offset_utf16": v.get("endOffset"),
                    "start_char": utf16_to_codepoint(msg["text"], v.get("startOffset", 0)),
                    "end_char": utf16_to_codepoint(msg["text"], v.get("endOffset", 0)),
                    "label_matches_role": ((v.get("paragraphlabels") or [""])[0] == "My words") == (msg["role"] == "user"),
                })
            elif "start" in v:
                spans.setdefault(rid, {}).setdefault("answers", {})[r["from_name"]] = _answer(r)
            else:
                conversation_answers[r["from_name"]] = _answer(r)
        for s in spans.values():
            s.setdefault("answers", {})
        conversations.append({
            "participant_id": row["study_id"],
            "task_id": row["task_id"],
            "conversation_id": data.get("conversation_id"),
            "title": data.get("title"),
            "source": row["source"],
            "codebook_version": data.get("codebook_version"),
            "imported_at": row["task_created_at"],
            "annotation_created_at": row["annotation_created_at"],
            "annotation_updated_at": row["annotation_updated_at"],
            "submitted": bool(row["submitted"]),
            "conversation_context": conversation_answers,
            "spans": [s for s in spans.values() if "message_id" in s],
            "messages": dialogue,
            "label_studio_result": results,
        })
    return {"schema": "typology-export-v1", "conversations": conversations}
