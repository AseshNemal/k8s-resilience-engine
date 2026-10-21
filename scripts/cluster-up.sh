#!/usr/bin/env bash
# Create (or reuse) the local kind research cluster and write a kubeconfig for the app container.
# Run `scripts/cluster-up.sh --help` for the options.
#
# This is only a launcher: the logic lives in cluster_up.py so macOS, Linux and Windows
# (scripts\\cluster-up.cmd) all run the same code.
set -eu

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for python in python3 python; do
  if command -v "$python" >/dev/null 2>&1; then
    exec "$python" "$here/cluster_up.py" "$@"
  fi
done
echo "ERROR: Python 3.9 or newer is required to run this script (python3 was not found on PATH)." >&2
exit 1
