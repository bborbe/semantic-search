---
status: active
---

# Scenario 002: HTTP server serves REST search

Validates that `semantic-search-http` binds a port, indexes a content directory, and returns valid JSON from `GET /search` — covering the real socket bind and HTTP contract that an in-process test client cannot exercise.

## Setup
- [ ] Working tree is the repo under change (`cd ~/Documents/workspaces/semantic-search`)
- [ ] `uv sync` has been run; `uv run semantic-search-http --help` works
- [ ] Prepare a content directory: `CONTENT_DIR=$(scenarios/helper/setup-content-dir.sh)`
- [ ] Port `18321` is free: `lsof -i :18321` returns nothing
- [ ] `curl` and `jq` are on PATH

## Action
- [ ] Start the server via the helper (starts, polls until ready, prints PID):
  ```bash
  PID=$(PORT=18321 CONTENT_PATH="$CONTENT_DIR" LOG_FILE=/tmp/scenario-002-server.log \
    scenarios/helper/start-http-server.sh)
  echo "$PID" > /tmp/scenario-002-server.pid
  ```
- [ ] Run the search and capture the response:
  ```bash
  curl -fsS "http://127.0.0.1:18321/search?q=kubernetes&top_k=3" \
    -o /tmp/scenario-002-response.json
  ```

## Expected
- [ ] `curl` exited 0 (HTTP 2xx)
- [ ] `/tmp/scenario-002-response.json` parses as JSON: `jq . /tmp/scenario-002-response.json > /dev/null` exits 0
- [ ] Response contains a `results` array with up to 3 entries: `jq '.results | type == "array" and length <= 3' /tmp/scenario-002-response.json` returns `true`
- [ ] Each result has `path` and `score` fields: `jq -e '.results[] | has("path") and has("score")' /tmp/scenario-002-response.json` exits 0
- [ ] Server log on stderr (in `/tmp/scenario-002-server.log`) shows startup line mentioning the port

## Cleanup
- `scenarios/helper/stop-server.sh $(cat /tmp/scenario-002-server.pid) && rm -f /tmp/scenario-002-server.pid`
- `rm -f /tmp/scenario-002-server.log /tmp/scenario-002-response.json`
- `rm -rf "$CONTENT_DIR"` if the helper created it
