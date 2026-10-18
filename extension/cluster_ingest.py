"""Command-line entry for the host helper: install or read the cluster credential over stdin.

    python -m extension.cluster_ingest            read {"kubeconfig": ..., "managed": ...} from stdin
    python -m extension.cluster_ingest --status   print the connection status
    python -m extension.cluster_ingest --disconnect   delete the stored credential

The helper reaches this through `docker exec -i -u 10001`, so the credential travels on stdin and
never appears in a process list. It runs exactly the checks of PUT /_extension/cluster (it calls the
same function), prints one JSON object, and never prints or logs the credential, not even on an
unexpected error: only the exception type is reported.
"""

import json
import sys

from fastapi import HTTPException
from pydantic import ValidationError

from extension import cluster_connection as cc

MAX_INPUT_BYTES = 256 * 1024


def _emit(payload: dict, code: int) -> int:
    sys.stdout.write(json.dumps(payload) + "\n")
    return code


def run(argv: list[str], stdin) -> int:
    if argv == ["--status"]:
        return _emit({"ok": True, **cc._status()}, 0)
    if argv == ["--disconnect"]:
        cc.KUBECONFIG_PATH.unlink(missing_ok=True)
        cc._managed_path().unlink(missing_ok=True)
        return _emit({"ok": True, **cc._status()}, 0)
    if argv:
        return _emit({"ok": False, "error": "usage: cluster_ingest [--status | --disconnect]"}, 2)

    raw = stdin.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        return _emit({"ok": False, "error": "The input is too large."}, 1)
    try:
        request = cc.ConnectRequest.model_validate_json(raw)
    except (ValidationError, ValueError):
        return _emit({"ok": False, "error": "The input is not a valid connect request."}, 1)

    try:
        status = cc.connect(request.kubeconfig, request.managed)
    except HTTPException as error:
        return _emit({"ok": False, "status": error.status_code, "error": str(error.detail)}, 1)
    except Exception as error:  # noqa: BLE001 - report the type only; the message could echo input
        return _emit({"ok": False, "error": f"unexpected error ({type(error).__name__})"}, 1)
    return _emit({"ok": True, **status}, 0)


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:], sys.stdin))
