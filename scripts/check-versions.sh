#!/usr/bin/env bash
#
# Verifies the four version strings of semantic-search are aligned. Exits
# non-zero on any mismatch with a clear report.
#
# The four strings:
#   1. CHANGELOG.md                          — top "## vX.Y.Z" entry
#   2. .claude-plugin/plugin.json            — .version
#   3. .claude-plugin/marketplace.json       — .metadata.version
#   4. .claude-plugin/marketplace.json       — .plugins[0].version
#
# The Python package version is NOT checked here — `hatch-vcs` derives it
# from the git tag at install time, so it is bound to the tag by definition.
#
# Run from repo root (Makefile target `check-versions`).

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

CHANGELOG_VERSION=$(grep -m1 '^## v' CHANGELOG.md | sed 's/^## v//')
PLUGIN_VERSION=$(jq -r .version .claude-plugin/plugin.json)
META_VERSION=$(jq -r .metadata.version .claude-plugin/marketplace.json)
PLUGINS0_VERSION=$(jq -r '.plugins[0].version' .claude-plugin/marketplace.json)

ok=true
report() {
  printf "  %-44s %s\n" "$1" "$2"
}

echo "Version alignment check"
report "CHANGELOG.md (top ## vX.Y.Z)"                       "$CHANGELOG_VERSION"
report ".claude-plugin/plugin.json .version"                "$PLUGIN_VERSION"
report ".claude-plugin/marketplace.json metadata.version"   "$META_VERSION"
report ".claude-plugin/marketplace.json plugins[0].version" "$PLUGINS0_VERSION"

if [ "$CHANGELOG_VERSION" != "$PLUGIN_VERSION" ] \
   || [ "$PLUGIN_VERSION"  != "$META_VERSION" ] \
   || [ "$META_VERSION"    != "$PLUGINS0_VERSION" ]; then
  ok=false
fi

if $ok; then
  echo "✅ all four versions equal: $CHANGELOG_VERSION"
  exit 0
else
  echo "❌ version mismatch"
  echo "    fix: update all four to the same value, then re-run."
  exit 1
fi
