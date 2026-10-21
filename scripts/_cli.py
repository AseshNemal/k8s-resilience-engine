"""Small shared pieces for the cluster scripts (cluster_up.py, extension_cluster_access.py).

These scripts used to be bash, which does not run on Windows. They now run on Python 3.9 or newer on macOS,
Linux and Windows, and only start the programs they need (docker, kind, kubectl) with an argument list,
never through a shell, so quoting and paths behave the same everywhere. Tests replace SystemShell with a
recording stand-in, so nothing here needs Docker or a cluster to be tested.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class Exit(Exception):
    """Stop the program with this exit code (raised instead of calling sys.exit deep inside)."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Captured:
    """The result of reading from a command: code is None when the command did not answer in time."""

    code: int | None
    out: str


class SystemShell:
    """Runs real programs. Tests substitute a recording stand-in with the same three methods."""

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def capture(self, argv: list[str], cwd: Path, timeout: float, show_errors: bool = False) -> Captured:
        """Run a command and return its output. Its own error text is hidden unless ``show_errors`` is set."""
        try:
            done = subprocess.run(
                argv, cwd=str(cwd), stdout=subprocess.PIPE, stderr=None if show_errors else subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return Captured(None, "")
        except OSError:
            return Captured(127, "")
        return Captured(done.returncode, done.stdout)

    def run(self, argv: list[str], cwd: Path) -> int:
        try:
            return subprocess.run(argv, cwd=str(cwd)).returncode
        except OSError as error:
            print(f"ERROR: could not start {argv[0]}: {error.strerror or error}", file=sys.stderr)
            return 127


def die(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise Exit(code)


def posix_only_chmod(path: Path, mode: int) -> None:
    """Restrict a file or folder on systems that have Unix permissions. Windows has no equivalent mode bits;
    there the file lives in the user's own profile folder tree and inherits its access rules."""
    if os.name != "nt":
        os.chmod(str(path), mode)


def write_private_file(path: Path, text: str) -> None:
    """Write ``text`` so that the file is never readable by anyone else, not even for an instant."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(str(path), flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    posix_only_chmod(path, 0o600)
