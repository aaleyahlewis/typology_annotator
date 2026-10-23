#!/usr/bin/env bash
# End-to-end test against a REAL Label Studio server.
#
#   LS_URL=http://localhost:8080 LS_TOKEN=<legacy API token> scripts/run_e2e.sh
#
# Creates a fresh Label Studio project, starts the local share-page stand-in and the
# app (APP_ENV=test), runs the browser flow, then deletes the test project.
# Use a test Label Studio instance, never the one holding participant data.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${LS_URL:?set LS_URL}" "${LS_TOKEN:?set LS_TOKEN (legacy token)}"
TMP=$(mktemp -d)
APP_PORT=8799 SHARE_PORT=8798
cleanup() {
  kill ${APP:-} ${SHARE:-} 2>/dev/null || true
  [ -n "${PROJECT:-}" ] && curl -s -o /dev/null -X DELETE -H "Authorization: Token $LS_TOKEN" "$LS_URL/api/projects/$PROJECT/" || true
  rm -rf "$TMP"
}
trap cleanup EXIT

PROJECT=$(python3 - "$LS_URL" "$LS_TOKEN" <<'EOF'
import json, sys, urllib.request
url, token = sys.argv[1:]
body = json.dumps({"title": "typology e2e (temporary)", "label_config": open("config/labeling_config.xml").read()}).encode()
req = urllib.request.Request(url + "/api/projects/", data=body, method="POST",
                             headers={"Authorization": f"Token {token}", "Content-Type": "application/json"})
print(json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req))["id"])
EOF
)
echo "Label Studio test project: $PROJECT"

export APP_ENV=test HTTPS_ONLY=0 SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export ADMIN_TOKEN=e2e-admin TYPOLOGY_DB="$TMP/e2e.sqlite3" EXPORT_DIR="$TMP/exports"
export LABEL_STUDIO_URL="$LS_URL" LABEL_STUDIO_API_KEY="$LS_TOKEN" LABEL_STUDIO_PROJECT_ID="$PROJECT"
export TEST_SHARE_ORIGIN="http://127.0.0.1:$SHARE_PORT"
P1_CODE=$(.venv/bin/python scripts/manage.py add-participant E2E01 | awk '/Access code/ {print $3}')
P2_CODE=$(.venv/bin/python scripts/manage.py add-participant E2E02 | awk '/Access code/ {print $3}')
.venv/bin/python scripts/manage.py check

python3 tests/e2e/share_server.py $SHARE_PORT & SHARE=$!
.venv/bin/uvicorn app.main:app --port $APP_PORT >"$TMP/app.log" 2>&1 & APP=$!
for _ in $(seq 50); do curl -s -o /dev/null --noproxy localhost http://localhost:$APP_PORT/ && break; sleep 0.2; done

BASE=http://localhost:$APP_PORT LS_URL="$LS_URL" LS_TOKEN="$LS_TOKEN" LS_PROJECT="$PROJECT" ADMIN_TOKEN=e2e-admin EXPORT_DIR="$EXPORT_DIR" \
  P1=E2E01 P1_CODE="$P1_CODE" P2=E2E02 P2_CODE="$P2_CODE" SHARE_ID=6729a1b2-0000-4000-8000-123456789abc \
  OUT=test-results NO_PROXY=localhost,127.0.0.1 node tests/e2e/flow.cjs || { echo "--- app log (errors) ---"; grep -v " 200 OK\| 202 Accepted\|^INFO:     Started\|^INFO:     Waiting\|^INFO:     Application\|^INFO:     Uvicorn" "$TMP/app.log" | tail -30; exit 1; }
