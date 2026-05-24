---
status: active
---

# Scenario 004: HTTP /content happy paths

Validates that `semantic-search-http` serves `GET /content` over a real socket for the documented success modes — full file and query-focused snippet. Covers the remote-deployment claim (the whole point of the feature) which in-process `TestClient` does not exercise.

## Setup
- [ ] Working tree is the repo under change (`cd ~/Documents/workspaces/semantic-search`)
- [ ] `uv sync` has been run; `uv run semantic-search-http --help` works
- [ ] Prepare a content directory: `CONTENT_DIR=$(scenarios/helper/setup-content-dir.sh)`
- [ ] Port `18322` is free: `lsof -i :18322` returns nothing
- [ ] `curl` and `jq` are on PATH

## Action
- [ ] Start the server via the helper:
  ```bash
  PID=$(PORT=18322 CONTENT_PATH="$CONTENT_DIR" LOG_FILE=/tmp/scenario-004-server.log \
    scenarios/helper/start-http-server.sh)
  echo "$PID" > /tmp/scenario-004-server.pid
  ```
- [ ] Full-mode fetch — entire `kubernetes.md`:
  ```bash
  curl -fsS "http://127.0.0.1:18322/content?path=${CONTENT_DIR}/kubernetes.md" \
    -o /tmp/scenario-004-full.json
  ```
- [ ] Snippet-mode fetch — focused window around "autoscaling":
  ```bash
  curl -fsS "http://127.0.0.1:18322/content?path=${CONTENT_DIR}/kubernetes.md&snippet=true&query=autoscaling&context_lines=0" \
    -o /tmp/scenario-004-snippet.json
  ```

## Expected
- [ ] Full mode body parses as JSON and has `path`, `content`, `mode == "full"`: `jq -e '.path and .content and .mode == "full"' /tmp/scenario-004-full.json` exits 0
- [ ] Full-mode `content` includes the fixture title: `jq -e '.content | contains("Kubernetes deployment notes")' /tmp/scenario-004-full.json` exits 0
- [ ] Snippet mode body has `mode == "snippet"` and contains the queried token: `jq -e '.mode == "snippet" and (.content | contains("autoscaling"))' /tmp/scenario-004-snippet.json` exits 0
- [ ] Snippet content is strictly shorter than full content (proves narrowing actually happened): `jq -es '.[0].content | length as $snip | input | .content | length > $snip' /tmp/scenario-004-snippet.json /tmp/scenario-004-full.json` exits 0
- [ ] Server log shows `/content` route at startup: `grep -q '/content' /tmp/scenario-004-server.log`

## Cleanup
- `scenarios/helper/stop-server.sh $(cat /tmp/scenario-004-server.pid) && rm -f /tmp/scenario-004-server.pid`
- `rm -f /tmp/scenario-004-server.log /tmp/scenario-004-full.json /tmp/scenario-004-snippet.json`
- `rm -rf "$CONTENT_DIR"` if the helper created it
