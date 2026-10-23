#!/usr/bin/env python3
"""Researcher commands. Run from the project folder with the same environment variables as the server.

  add-participant P01        create a participant; prints a one-time access code
  reset-code P01             new access code (signs out their open sessions)
  disable P01 / enable P01   block or restore a participant's access
  sync-config                set the Label Studio project's labeling config to config/labeling_config.xml
  check                      verify settings, Label Studio access and that the project config matches
  export FILE.json           write the research export (validated) to FILE.json
  expire-imports             discard pending imports older than PENDING_IMPORT_TTL_HOURS
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db, export, labeling_config, labelstudio, security  # noqa: E402
from app.settings import settings  # noqa: E402


async def check():
    ok = True
    for p in settings.check():
        print("PROBLEM:", p)
        ok = False
    cfg = labeling_config.load()
    print(f"Labeling config: {settings.labeling_config_path} (sha256 {cfg.sha256[:12]}…)")
    try:
        project = await labelstudio.client().get_project()
        print(f"Label Studio: connected, project {settings.ls_project_id} '{project.get('title')}'")
        if (project.get("label_config") or "").strip() != cfg.xml.strip():
            print("PROBLEM: the project's labeling config differs from config/labeling_config.xml. Run: sync-config")
            ok = False
        else:
            print("Labeling config matches.")
    except labelstudio.LabelStudioError as e:
        print(f"PROBLEM: Label Studio: {e} (status {e.status})")
        ok = False
    print("OK" if ok else "Fix the problems above before participants use the system.")
    return ok


def main(argv):
    db.init()
    cmd, arg = (argv[1] if len(argv) > 1 else None), (argv[2] if len(argv) > 2 else None)
    if cmd == "add-participant" and arg:
        code = security.new_access_code()
        db.add_participant(arg, security.hash_code(code))
        print(f"Study ID: {arg}\nAccess code: {code}\n(Only shown once. Share it through your approved channel.)")
    elif cmd == "reset-code" and arg:
        code = security.new_access_code()
        if not db.set_code(arg, security.hash_code(code)):
            sys.exit(f"No participant {arg}")
        print(f"New access code for {arg}: {code}")
    elif cmd in ("disable", "enable") and arg:
        if not db.set_disabled(arg, cmd == "disable"):
            sys.exit(f"No participant {arg}")
        print(f"{arg} {cmd}d")
    elif cmd == "sync-config":
        asyncio.run(labelstudio.client().set_label_config(labeling_config.load().xml))
        print(f"Project {settings.ls_project_id} now uses config/labeling_config.xml")
    elif cmd == "check":
        sys.exit(0 if asyncio.run(check()) else 1)
    elif cmd == "export" and arg:
        data = asyncio.run(export.build_export())
        fd = os.open(arg, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        s = data["summary"]
        print(f"Wrote {s['conversations']} conversation(s), {s['annotations']} highlight(s) to {arg}")
        if s["conversations_with_issues"]:
            print("Needs review (see validation.issues):", ", ".join(s["conversations_with_issues"]))
    elif cmd == "expire-imports":
        print(f"Expired {db.expire_pending_imports(settings.pending_import_ttl_hours)} pending import(s)")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
