#!/usr/bin/env bash
#
# Send a single JSON-RPC `initialize` request to `semantic-search-mcp serve`,
# capture stdout and stderr to separate files, then exit.
#
# Usage:
#   CONTENT_PATH=/tmp/scenario-content \
#     scenarios/helper/probe-mcp.sh OUT_FILE ERR_FILE
#
# Example:
#   CONTENT_PATH=/tmp/scenario-content \
#     scenarios/helper/probe-mcp.sh /tmp/mcp-out.txt /tmp/mcp-err.txt

set -euo pipefail

: "${CONTENT_PATH:?CONTENT_PATH env var required}"
OUT_FILE=${1:?usage: probe-mcp.sh OUT_FILE ERR_FILE}
ERR_FILE=${2:?usage: probe-mcp.sh OUT_FILE ERR_FILE}
TIMEOUT=${TIMEOUT:-30}

rm -f "$OUT_FILE" "$ERR_FILE"

INIT_REQUEST='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe-mcp","version":"1.0"}}}'

printf '%s\n' "$INIT_REQUEST" \
  | CONTENT_PATH="$CONTENT_PATH" timeout "$TIMEOUT" uv run semantic-search-mcp serve \
  > "$OUT_FILE" \
  2> "$ERR_FILE"
