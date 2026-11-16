"""Linux extension launcher: one backend owns the data volume, including during migrations."""

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

LOCK_PATH = Path("/data/backend.lock")
SOCKET_PATH = Path("/run/guest-services/k8s-resilience-engine.sock")


class BackendAlreadyRunning(RuntimeError):
    pass


@contextmanager
def single_backend(path: Path):
    # The extension runs in a Linux container on every host OS. This lock is on the shared data volume,
    # so a second container/process cannot evade the in-process Kubernetes mutation lock.
    import fcntl

    with path.open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackendAlreadyRunning("Another backend owns this data volume; refusing to start a second writer.") from None
        # Keep the lock through exec into the one uvicorn worker; process exit/crash releases it automatically.
        os.set_inheritable(lock.fileno(), True)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main() -> int:
    try:
        with single_backend(LOCK_PATH):
            SOCKET_PATH.unlink(missing_ok=True)
            subprocess.run(["alembic", "upgrade", "head"], check=True)
            os.execvp("uvicorn", ["uvicorn", "extension.asgi:application", "--workers", "1", "--uds", str(SOCKET_PATH)])
    except BackendAlreadyRunning as error:
        print(str(error), file=sys.stderr)
        return 73
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
