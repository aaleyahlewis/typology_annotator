#!/usr/bin/env python3
"""Researcher commands.

  python scripts/manage.py add-participant P01     # prints a new access code
  python scripts/manage.py reset-code P01
  python scripts/manage.py export typology_export.json
"""
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db, export  # noqa: E402
from app.main import hash_code  # noqa: E402


def new_code():
    words = secrets.token_hex(3), secrets.token_hex(3), secrets.token_hex(3)
    return "-".join(words)


def main(argv):
    db.init()
    if len(argv) == 3 and argv[1] == "add-participant":
        code = new_code()
        db.add_participant(argv[2], hash_code(code))
        print(f"Study ID: {argv[2]}\nAccess code: {code}\n(Give this to the participant. It isn't stored and can't be shown again.)")
    elif len(argv) == 3 and argv[1] == "reset-code":
        code = new_code()
        with db.connect() as c:
            if not c.execute("UPDATE participants SET code_hash = ? WHERE study_id = ?", (hash_code(code), argv[2])).rowcount:
                sys.exit(f"No participant {argv[2]}")
        print(f"New access code for {argv[2]}: {code}")
    elif len(argv) == 3 and argv[1] == "export":
        data = export.build_export()
        Path(argv[2]).write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"Wrote {len(data['conversations'])} conversation(s) to {argv[2]}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
