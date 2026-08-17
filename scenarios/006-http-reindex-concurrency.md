---
status: active
---

# Scenario 006: HTTP /reindex concurrency guard

Validates over a real socket that `GET /reindex` routes through the compaction guard: a manual reindex returns 200 and actually rebuilds, while a second `/reindex` issued while the first is still running returns HTTP 409 with nested error code `REINDEX_IN_PROGRESS` instead of starting a second rebuild. Covers the wire-level guarantee that in-process tests cannot certify — in particular that the busy response survives serialization.

## Setup
- [ ] Working tree is the repo under change (`cd ~/Documents/workspaces/semantic-search`)
- [ ] `uv sync` has been run; `uv run semantic-search-http --help` works
- [ ] Port `18324` is free: `lsof -i :18324` returns nothing
- [ ] `curl` and `jq` are on PATH
- [ ] Create a large corpus so the manual rebuild stays in flight long enough to observe mid-flight (1500 files ≈ multiple seconds of embedding):
  ```bash
  REINDEX_DIR=/tmp/scenario-006-corpus
  rm -rf "$REINDEX_DIR" && mkdir -p "$REINDEX_DIR"
  for i in $(seq 1 1500); do
    printf '# Note %d\n\nContent for scenario 006 reindex concurrency.\n' "$i" \
      > "$REINDEX_DIR/note-$i.md"
  done
  ```

## Action
- [ ] Start the server via the helper (READY_TIMEOUT raised: the initial build re-embeds all 1500 files):
  ```bash
  PID=$(PORT=18324 CONTENT_PATH="$REINDEX_DIR" LOG_FILE=/tmp/scenario-006-server.log \
    READY_TIMEOUT=180 scenarios/helper/start-http-server.sh)
  echo "$PID" > /tmp/scenario-006-server.pid
  ```
- [ ] Fire the first reindex in the background, then wait until the server log confirms the manual rebuild is genuinely in flight (the guard-acquire INFO line `Manual reindex requested` is emitted before `rebuild_index` runs):
  ```bash
  curl -s -o /tmp/scenario-006-first.json -w "%{http_code}" \
    "http://127.0.0.1:18324/reindex" > /tmp/scenario-006-first.status &
  FIRST_PID=$!

  for _ in $(seq 1 120); do
    grep -q "Manual reindex requested" /tmp/scenario-006-server.log && break
    sleep 1
  done
  ```
- [ ] Issue the second reindex while the first is still rebuilding — capture status and body separately:
  ```bash
  STATUS=$(curl -s -o /tmp/scenario-006-second.json -w "%{http_code}" \
    "http://127.0.0.1:18324/reindex")
  echo -n "$STATUS" > /tmp/scenario-006-second.status
  ```
- [ ] Wait for the first reindex to finish:
  ```bash
  wait "$FIRST_PID"
  ```

## Expected
- [ ] The manual rebuild was observed in flight: `grep -q "Manual reindex requested" /tmp/scenario-006-server.log` exits 0
- [ ] First reindex returned HTTP 200: `[[ "$(cat /tmp/scenario-006-first.status)" == "200" ]]`
- [ ] First body is the success shape with `status == "ok"`: `jq -e '.status == "ok"' /tmp/scenario-006-first.json` exits 0
- [ ] Second reindex (while first was running) returned HTTP 409: `[[ "$(cat /tmp/scenario-006-second.status)" == "409" ]]`
- [ ] Second body carries the nested error code `REINDEX_IN_PROGRESS`: `jq -e '.error.code == "REINDEX_IN_PROGRESS"' /tmp/scenario-006-second.json` exits 0
- [ ] The busy response started no second rebuild — the manual-rebuild start log appears exactly once: `[[ "$(grep -c 'Manual reindex requested' /tmp/scenario-006-server.log)" == "1" ]]`
- [ ] Server log shows `/reindex` route at startup: `grep -q '/reindex' /tmp/scenario-006-server.log`

## Cleanup
- `scenarios/helper/stop-server.sh $(cat /tmp/scenario-006-server.pid) && rm -f /tmp/scenario-006-server.pid`
- `rm -f /tmp/scenario-006-server.log /tmp/scenario-006-first.json /tmp/scenario-006-first.status /tmp/scenario-006-second.json /tmp/scenario-006-second.status`
- `rm -rf "$REINDEX_DIR"`
