"""Real process locks: a second backend cannot migrate or remove a live socket."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from extension import runtime

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="The extension runtime is a Linux container")


def test_second_process_is_refused_and_crash_releases_lock(tmp_path):
    lock = tmp_path / "backend.lock"
    program = "from pathlib import Path;from extension.runtime import single_backend;import sys;\nwith single_backend(Path(sys.argv[1])): print('locked',flush=True);sys.stdin.read()"
    process = subprocess.Popen([sys.executable, "-c", program, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(runtime.BackendAlreadyRunning):
            with runtime.single_backend(lock):
                pytest.fail("a second writer acquired the volume")
    finally:
        process.kill()
        process.wait(5)
    with runtime.single_backend(lock):
        assert lock.exists()


def test_second_launcher_never_removes_socket_or_runs_migrations(tmp_path, monkeypatch):
    lock, socket = tmp_path / "backend.lock", tmp_path / "backend.sock"
    socket.write_text("existing socket sentinel")
    monkeypatch.setattr(runtime, "LOCK_PATH", lock)
    monkeypatch.setattr(runtime, "SOCKET_PATH", socket)
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **kw: pytest.fail("migration ran"))
    with runtime.single_backend(lock):
        assert runtime.main() == 73
    assert socket.read_text() == "existing socket sentinel"
