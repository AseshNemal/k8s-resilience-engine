#!/usr/bin/env bash
# Build, install, update, inspect and clean up the Resilience Engine Docker Desktop extension.
# Run `scripts/extension-manage.sh --help` for the commands and options.
#
# This is only a launcher: the logic lives in extension_manage.py so macOS, Linux and Windows
# (scripts\extension-manage.cmd) all run the same code.
set -eu

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for python in python3 python; do
  if command -v "$python" >/dev/null 2>&1; then
    exec "$python" "$here/extension_manage.py" "$@"
  fi
done
echo "ERROR: Python 3.9 or newer is required to run the extension manager (python3 was not found on PATH)." >&2
exit 1
