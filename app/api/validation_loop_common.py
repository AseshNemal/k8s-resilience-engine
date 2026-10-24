"""Small helpers shared by the validation-loop route modules (kept apart so the modules do not import each other in a circle)."""

import os
from pathlib import Path

from fastapi import HTTPException

def _first_kubeconfig() -> str:
    return next((p for p in (os.environ.get("KUBECONFIG") or "").split(os.pathsep) if p), "~/.kube/config")


def _validation_dir() -> Path:
    configured = os.environ.get("RESILIENCE_VALIDATION_DIR")
    return Path(configured) if configured else (Path("/data/validation") if Path("/data").is_dir() else Path("./data/validation"))


CLUSTER_WRITES_ENV = "RESILIENCE_ALLOW_CLUSTER_WRITES"


def require_cluster_writes_armed() -> None:
    """A run stops nodes and patches Deployments, so it needs the same deliberate arming as Apply and Reset.

    Read from the backend's own environment (the extension's compose file sets it), never from a request. Without it nothing that
    writes starts: not a run, not a recovery. The digest in a run request ties an approval to the plan that was reviewed; it is not
    a credential, and this tool has no login, so this deployment-level switch is what keeps a stray request from writing.
    """
    if os.environ.get(CLUSTER_WRITES_ENV) != "1":
        raise HTTPException(status_code=403, detail={
            "code": "CLUSTER_WRITES_NOT_ENABLED",
            "message": "This deployment has not armed changes to the cluster, so a test run cannot start. Planning and reading still work."})
