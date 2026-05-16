#!/usr/bin/env bash
#
# Create a small markdown corpus at $1 (default: /tmp/scenario-content) for use
# as CONTENT_PATH in semantic-search scenarios.
#
# Usage:
#   scenarios/helper/setup-content-dir.sh [TARGET_DIR]
#
# Prints the absolute path to the prepared directory on stdout.

set -euo pipefail

TARGET=${1:-/tmp/scenario-content}

rm -rf "$TARGET"
mkdir -p "$TARGET"

cat > "$TARGET/kubernetes.md" <<'EOF'
# Kubernetes deployment notes

Notes on kubernetes pods, services, deployments. Cluster orchestration,
container scheduling, rolling updates, and horizontal pod autoscaling.
EOF

cat > "$TARGET/python.md" <<'EOF'
# Python testing notes

pytest fixtures, capsys for capturing stdout/stderr, async tests with
pytest-asyncio, mocking with unittest.mock.
EOF

cat > "$TARGET/docker.md" <<'EOF'
# Docker container notes

Docker containers, base images, multi-stage builds, registries,
container networking, and volume mounts.
EOF

echo "$TARGET"
