#!/usr/bin/env bash
#
# Kill a server started by start-http-server.sh. Tolerates already-dead PIDs.
#
# Usage:
#   scenarios/helper/stop-server.sh PID

set -euo pipefail

PID=${1:?usage: stop-server.sh PID}

if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  # Give it a moment to release the port.
  for _ in 1 2 3 4 5; do
    kill -0 "$PID" 2>/dev/null || exit 0
    sleep 1
  done
  # Still alive — escalate.
  kill -9 "$PID" 2>/dev/null || true
fi
exit 0
