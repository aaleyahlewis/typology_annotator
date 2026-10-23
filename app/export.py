"""TYPOLOGY_DATA_EXPORT: research export joining Label Studio results with the canonical record.

For each highlight the export gives the message it came from (by message_id), the
exact text, and offsets in both units:
  * span.start / span.end    Python string indexes: messages[i]["text"][start:end] == span.text
  * span.start_utf16 / end_utf16   Label Studio's own (JavaScript) offsets
Every question in a highlight's panel appears in `answers`; unanswered ones are null.
Problems are reported in `validation.issues`, never silently fixed.
"""
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import annotations, db, labeling_config, labelstudio
from .settings import settings

EXPORT_SCHEMA = "typology-export-1.0"
COMBINED_NAME = "typology_export.json"
log = logging.getLogger("typology")


def _answer(r):
    v = r.get("value") or {}
    if r.get("type") == "choices":
        return v.get("choices")
    if r.get("type") == "textarea":
        texts = v.get("text") or []
        return texts[0] if len(texts) == 1 else texts
    return None


def transform(row, result):
    cfg = labeling_config.load()
    canonical = json.loads(row["canonical_json"])
    dialogue = canonical["dialogue"]
    panel_fields = {label: sorted(n for n, c in cfg.controls.items() if c.get("label") == label)
                    for label in (cfg.user_label, cfg.assistant_label)}
    conversation_fields = sorted(n for n, c in cfg.controls.items() if c["type"] != "paragraphlabels" and not c["per_region"])
    issues, spans, answers_by_region, context = [], {}, {}, {f: None for f in conversation_fields}

    for r in result or []:
        rid, v = r.get("id"), r.get("value") or {}
        if r.get("from_name") == cfg.span_control:
            idx = int(v.get("start", -1)) if str(v.get("start", "")).isdigit() else -1
            if not 0 <= idx < len(dialogue):
                issues.append({"region_id": rid, "problem": "message_not_found"})
                continue
            msg = dialogue[idx]
            so, eo = v.get("startOffset"), v.get("endOffset")
            text = annotations.span_text(msg["text"], so, eo) if isinstance(so, int) and isinstance(eo, int) else None
            label = (v.get("paragraphlabels") or [None])[0]
            if text is None or text != v.get("text"):
                issues.append({"region_id": rid, "problem": "text_mismatch"})
            if str(v.get("end")) != str(v.get("start")):
                issues.append({"region_id": rid, "problem": "spans_multiple_messages"})
            expected = cfg.user_label if msg["role"] == "user" else cfg.assistant_label
            if label != expected:
                issues.append({"region_id": rid, "problem": "label_role_mismatch"})
            a = annotations.utf16_to_index(msg["text"], so) if isinstance(so, int) else None
            b = annotations.utf16_to_index(msg["text"], eo) if isinstance(eo, int) else None
            spans[rid] = {
                "annotation_id": row["ls_annotation_id"],
                "region_id": rid,
                "message_id": msg["message_id"],
                "message_role": msg["role"],
                "message_index": idx,
                "label": label,
                "span": {"start": a, "end": b, "text": v.get("text"), "start_utf16": so, "end_utf16": eo},
            }
        elif "start" in v:
            answers_by_region.setdefault(rid, {})[r.get("from_name")] = _answer(r)
        elif r.get("from_name") in context:
            context[r["from_name"]] = _answer(r)

    for rid, answers in answers_by_region.items():
        if rid not in spans:
            issues.append({"region_id": rid, "problem": "answers_without_highlight"})
    out_spans = []
    for rid, s in spans.items():
        fields = panel_fields.get(s["label"], [])
        given = answers_by_region.get(rid, {})
        for name in given:
            if name not in fields:
                issues.append({"region_id": rid, "problem": f"answer_for_other_panel:{name}"})
        s["answers"] = {f: given.get(f) for f in fields}
        out_spans.append(s)
    out_spans.sort(key=lambda s: (s["message_index"], s["span"]["start"] or 0, s["span"]["end"] or 0))

    return {
        "participant_id": row["study_id"],
        "conversation_id": row["id"],
        "label_studio_task_id": row["ls_task_id"],
        "label_studio_annotation_id": row["ls_annotation_id"],
        "title": row["title"],
        "source": row["source"],
        "extraction_method": row["method"],
        "extraction_warnings": json.loads(row["warnings_json"] or "[]"),
        "excluded_source_indexes": canonical.get("excluded_source_indexes", []),
        "schema_version": row["schema_version"],
        "label_config_sha256": row["label_config_sha"],
        "imported_at": row["created_at"],
        "annotation_created_at": row["annotation_created_at"],
        "annotation_updated_at": row["annotation_updated_at"],
        "submitted_at": row["submitted_at"],
        "conversation_context": context,
        "annotations": out_spans,
        "messages": [{k: m[k] for k in ("message_id", "index", "source_index", "role", "text")} for m in dialogue],
        "validation": {"ok": not issues, "issues": issues},
    }


async def build_export(client=None):
    client = client or labelstudio.client()
    conversations = []
    for row in db.export_rows():
        if row["status"] != "ready":
            continue
        result = None
        if row["ls_annotation_id"]:
            result = (await client.get_annotation(row["ls_annotation_id"]))["result"]
        conversations.append(transform(row, result))
    return {
        "export_schema": EXPORT_SCHEMA,
        "schema_version": settings.schema_version,
        "label_config_sha256": labeling_config.load().sha256,
        "conversations": conversations,
        "summary": {
            "conversations": len(conversations),
            "annotations": sum(len(c["annotations"]) for c in conversations),
            "conversations_with_issues": [c["conversation_id"] for c in conversations if not c["validation"]["ok"]],
        },
    }


# ---------- automatic export when a participant submits ----------
#
# EXPORT_DIR/conversations/<participant>__<conversation>.json   one file per submitted conversation
# EXPORT_DIR/typology_export.json                              all of them, same format as manage.py export
#
# Files are written atomically with owner-only permissions. Removing a conversation removes its file.

def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:120]


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def conversation_file(export_dir, study_id, conversation_id):
    return Path(export_dir) / "conversations" / f"{_safe(study_id)}__{_safe(conversation_id)}.json"


def rebuild_combined(export_dir):
    folder = Path(export_dir) / "conversations"
    conversations = []
    for f in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            conversations.append(json.loads(f.read_text(encoding="utf-8")))
        except ValueError:
            log.error("unreadable export file %s", f.name)
    conversations.sort(key=lambda c: c.get("imported_at") or "")
    _write_json(Path(export_dir) / COMBINED_NAME, {
        "export_schema": EXPORT_SCHEMA,
        "schema_version": settings.schema_version,
        "label_config_sha256": labeling_config.load().sha256,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "conversations": conversations,
        "summary": {
            "conversations": len(conversations),
            "annotations": sum(len(c["annotations"]) for c in conversations),
            "conversations_with_issues": [c["conversation_id"] for c in conversations if not c["validation"]["ok"]],
        },
    })


def export_submitted(conversation_id, result):
    """Write the export for one conversation right after the participant submits it."""
    export_dir = settings.export_dir
    if not export_dir:
        return None
    row = db.export_row(conversation_id)
    if row is None or row["status"] != "ready":
        return None
    entry = transform(row, result)
    path = conversation_file(export_dir, row["study_id"], conversation_id)
    _write_json(path, entry)
    rebuild_combined(export_dir)
    return path


def remove_export(study_id, conversation_id):
    export_dir = settings.export_dir
    if not export_dir:
        return
    path = conversation_file(export_dir, study_id, conversation_id)
    if path.exists():
        path.unlink()
        rebuild_combined(export_dir)
