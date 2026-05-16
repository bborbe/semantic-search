#!/usr/bin/env bash
#
# Start `semantic-search-http` on the given port against the given CONTENT_PATH,
# wait until /search responds, then print the server PID to stdout.
# Server stderr+stdout is redirected to $LOG_FILE (default /tmp/scenario-http.log).
#
# Usage:
#   PORT=18321 CONTENT_PATH=/tmp/scenario-content \
#     scenarios/helper/start-http-server.sh > /tmp/scenario-http.pid
#
# Honored env vars:
#   PORT          (required)
#   CONTENT_PATH  (required, comma-separated paths accepted)
#   LOG_FILE      (default /tmp/scenario-http.log)
#   READY_TIMEOUT (default 30 seconds)

set -euo pipefail

: "${PORT:?PORT env var required}"
: "${CONTENT_PATH:?CONTENT_PATH env var required}"
LOG_FILE=${LOG_FILE:-/tmp/scenario-http.log}
READY_TIMEOUT=${READY_TIMEOUT:-30}

# Refuse if port already busy — clearer error than a silent bind failure.
if lsof -i ":$PORT" >/dev/null 2>&1; then
  echo "ERROR: port $PORT is busy" >&2
  exit 1
fi

# uv run is what scenarios use against the current source tree; do not switch to
# the installed binary here.
CONTENT_PATH="$CONTENT_PATH" uv run semantic-search-http --port "$PORT" \
  > "$LOG_FILE" 2>&1 &
PID=$!

# Wait for readiness. Poll a known endpoint; HEAD-style 4xx counts as alive too.
for _ in $(seq 1 "$READY_TIMEOUT"); do
  if curl -fsS "http://127.0.0.1:$PORT/search?q=test&top_k=1" -o /dev/null 2>/dev/null; then
    echo "$PID"
    exit 0
  fi
  # If the server has already died, fail fast with the tail of the log.
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "ERROR: server pid $PID died during startup" >&2
    tail -20 "$LOG_FILE" >&2 || true
    exit 1
  fi
  sleep 1
done

echo "ERROR: server pid $PID did not become ready within ${READY_TIMEOUT}s" >&2
kill "$PID" 2>/dev/null || true
tail -20 "$LOG_FILE" >&2 || true
exit 1
