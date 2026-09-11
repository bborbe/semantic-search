#!/usr/bin/env bash
#
# Start `semantic-search-http` on the given port against the given CONTENT_PATH,
# wait until /health reports ready, then print the server PID to stdout.
# Server stderr+stdout is redirected to $LOG_FILE (default /tmp/scenario-http.log).
#
# `CONTENT_PATH` is a no-op for `semantic-search-http` since the per-vault scoping
# change: the daemon takes its index roots from the scope map named by
# `SEMANTIC_SCOPE_MAP`. So this helper derives a throwaway one-scope map from
# CONTENT_PATH (scope name `scenario`) and points the server at that — which also
# keeps a scenario run off the real vaults, since the committed scopes.yaml would
# otherwise index every configured vault. Scenario requests carry `&scope=scenario`.
#
# Usage:
#   PORT=18321 CONTENT_PATH=/tmp/scenario-content \
#     scenarios/helper/start-http-server.sh > /tmp/scenario-http.pid
#
# Honored env vars:
#   PORT          (required)
#   CONTENT_PATH  (required, comma-separated paths — becomes scope `scenario`)
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

# Derive a throwaway scope map from CONTENT_PATH so the server indexes only the
# scenario corpus. Left on disk (not trap-deleted) so a failed run stays inspectable
# and the backgrounded server keeps a valid path.
SCOPE_MAP=$(mktemp -t scenario-scopes.XXXXXX.yaml)
{
  echo "scopes:"
  echo "  scenario:"
  tr ',' '\n' <<< "$CONTENT_PATH" | sed 's/^[[:space:]]*/    - /'
} > "$SCOPE_MAP"

# uv run is what scenarios use against the current source tree; do not switch to
# the installed binary here.
CONTENT_PATH="$CONTENT_PATH" SEMANTIC_SCOPE_MAP="$SCOPE_MAP" \
  uv run semantic-search-http --port "$PORT" \
  > "$LOG_FILE" 2>&1 &
PID=$!

# Wait for readiness. Probe /health and require "ready": true — a bare status check
# is not enough, because /health answers 200 with "ready": false while the initial
# index build is still in flight. /health stays scopeless, so this needs no scope.
for _ in $(seq 1 "$READY_TIMEOUT"); do
  if curl -fsS "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ready": *true'; then
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
