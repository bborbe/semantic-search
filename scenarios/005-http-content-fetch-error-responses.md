---
status: active
---

# Scenario 005: HTTP /content error responses

Validates that `semantic-search-http` returns the documented nested error shape over a real socket for the two contract-critical failure modes — path traversal outside indexed roots (`PATH_OUTSIDE_ROOTS`, 400) and missing file inside roots (`FILE_NOT_FOUND`, 404). Covers wire-format guarantees that in-process tests cannot certify.

## Setup
- [ ] Working tree is the repo under change (`cd ~/Documents/workspaces/semantic-search`)
- [ ] `uv sync` has been run; `uv run semantic-search-http --help` works
- [ ] Prepare a content directory: `CONTENT_DIR=$(scenarios/helper/setup-content-dir.sh)`
- [ ] Port `18323` is free: `lsof -i :18323` returns nothing
- [ ] `curl` and `jq` are on PATH

## Action
- [ ] Start the server via the helper:
  ```bash
  PID=$(PORT=18323 CONTENT_PATH="$CONTENT_DIR" LOG_FILE=/tmp/scenario-005-server.log \
    scenarios/helper/start-http-server.sh)
  echo "$PID" > /tmp/scenario-005-server.pid
  ```
- [ ] Path-traversal attempt (`/etc/passwd`) — capture status and body separately:
  ```bash
  STATUS=$(curl -s -o /tmp/scenario-005-traversal.json -w "%{http_code}" \
    "http://127.0.0.1:18323/content?path=/etc/passwd")
  echo -n "$STATUS" > /tmp/scenario-005-traversal.status
  ```
- [ ] Missing-file attempt (path inside vault, file absent):
  ```bash
  STATUS=$(curl -s -o /tmp/scenario-005-missing.json -w "%{http_code}" \
    "http://127.0.0.1:18323/content?path=${CONTENT_DIR}/does-not-exist.md")
  echo -n "$STATUS" > /tmp/scenario-005-missing.status
  ```

## Expected
- [ ] Traversal returns HTTP 400: `[[ "$(cat /tmp/scenario-005-traversal.status)" == "400" ]]`
- [ ] Traversal body has nested error code `PATH_OUTSIDE_ROOTS`: `jq -e '.error.code == "PATH_OUTSIDE_ROOTS"' /tmp/scenario-005-traversal.json` exits 0
- [ ] Missing file returns HTTP 404: `[[ "$(cat /tmp/scenario-005-missing.status)" == "404" ]]`
- [ ] Missing-file body has nested error code `FILE_NOT_FOUND`: `jq -e '.error.code == "FILE_NOT_FOUND"' /tmp/scenario-005-missing.json` exits 0

## Cleanup
- `scenarios/helper/stop-server.sh $(cat /tmp/scenario-005-server.pid) && rm -f /tmp/scenario-005-server.pid`
- `rm -f /tmp/scenario-005-server.log /tmp/scenario-005-{traversal,missing}.{json,status}`
- `rm -rf "$CONTENT_DIR"` if the helper created it
