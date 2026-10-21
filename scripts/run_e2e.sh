#!/usr/bin/env bash
# Start a throwaway server with a fresh database and run the browser flow test.
set -euo pipefail
cd "$(dirname "$0")/.."
TMP=$(mktemp -d)
trap 'kill $SERVER 2>/dev/null || true; rm -rf "$TMP"' EXIT

export TYPOLOGY_DB="$TMP/e2e.sqlite3" ADMIN_TOKEN=e2e-admin SESSION_SECRET=e2e
CODE=$(.venv/bin/python scripts/manage.py add-participant E2E01 | awk '/Access code/ {print $3}')
python3 - "$TMP/export.zip" <<'EOF'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_DEFLATED) as z:
    z.write("tests/fixtures/fake_conversations.json", "conversations.json")
EOF

.venv/bin/uvicorn app.main:app --port 8799 >"$TMP/server.log" 2>&1 &
SERVER=$!
for _ in $(seq 50); do curl -s -o /dev/null --noproxy localhost http://localhost:8799/ && break; sleep 0.2; done

BASE=http://localhost:8799 STUDY_ID=E2E01 CODE="$CODE" EXPORT_ZIP="$TMP/export.zip" OUT=test-results \
  node tests/e2e/flow.cjs
