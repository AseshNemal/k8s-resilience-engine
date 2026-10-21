#!/usr/bin/env bash
# Create or revoke the READ-ONLY, time-limited credential the Docker Desktop extension uses.
# Run `scripts/extension-cluster-access.sh --help` for the options.
#
# This is only a launcher: the logic lives in extension_cluster_access.py so macOS, Linux and Windows
# (scripts\\extension-cluster-access.cmd) all run the same code.
set -eu

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for python in python3 python; do
  if command -v "$python" >/dev/null 2>&1; then
    exec "$python" "$here/extension_cluster_access.py" "$@"
  fi
done
echo "ERROR: Python 3.9 or newer is required to run this script (python3 was not found on PATH)." >&2
exit 1
