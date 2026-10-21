#!/usr/bin/env python3
"""Build, install, update, inspect and clean up the Resilience Engine Docker Desktop extension.

  extension-manage install   [--version X.Y.Z]   build the image and install the extension
  extension-manage update    [--version X.Y.Z]   build a new version and replace the installed one
                                                 (older local images are deleted, keeping the previous one)
  extension-manage uninstall                     remove the extension (asks first)
  extension-manage status                        installed version, state and local image tags
  extension-manage check                         run the test suites that guard the extension
  extension-manage clean                         delete old local image tags (asks first)

  Options: --version X.Y.Z  tag to build (default: 0.1.<number of commits>, so it follows git; with
           uncommitted changes, or when that tag is taken, the next version nobody has used)
           --keep-all       after an update, keep every old local image (default: keep the new one and
                            the previous one for a rollback, delete the rest)
           --skip-checks    do not run the test suites before building
           --dry-run        print every command instead of running it
           --yes            do not ask for confirmation

Run it as scripts/extension-manage.sh (macOS, Linux) or scripts\\extension-manage.cmd (Windows); both
start this file. It only needs Python 3.9 or newer and the Docker CLI, plus Go and Node to run `check`.

This only manages the Docker extension and its image. It never touches your Kubernetes cluster or its
access objects: approving cluster access happens in the extension's own dialog, and
scripts/extension-cluster-access.sh --revoke removes it. An update keeps the extension's data volume.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info < (3, 9):  # pragma: no cover - only reachable on an unsupported interpreter
    sys.exit("ERROR: Python 3.9 or newer is required to run the extension manager.")

IMAGE = "k8s-resilience-engine"
EXTENSION_ID = "k8s-resilience-engine"
DOCKERFILE = "extension/Dockerfile"
REPO_ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ("install", "update", "uninstall", "status", "check", "clean")

# A build that runs the Go compiler and imports the whole Python app needs room. The image build is
# arranged to avoid peaks, but a Docker VM below this still swaps badly. The default can be changed
# with RESILIENCE_MIN_DOCKER_MEMORY_GB; it only ever produces a warning.
DEFAULT_MIN_DOCKER_MEMORY_GB = 3.0
DOCKER_ANSWER_SECONDS = 30  # a healthy daemon answers `docker info` in well under a second
DOCKER_READ_SECONDS = 120

# The version follows git: BASE_VERSION.<number of commits>, so every machine on the same commit builds the
# same version and a `git pull` moves it forward by itself. The tag stays a plain X.Y.Z because Docker Desktop
# shows it as the extension's version and VERSION_PATTERN below is what recognises our tags.
BASE_VERSION = "0.1"
# Which commit an image was built from, stored as image metadata (the tag cannot carry it).
COMMIT_LABEL = "org.opencontainers.image.revision"
# ...and on which branch. Two branches can reach the same version number with different code, so the number
# is only trustworthy together with the commit and the branch it was built from.
BRANCH_LABEL = "org.opencontainers.image.ref.name"
GIT_READ_SECONDS = 15

VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
VersionKey = tuple


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

    def capture(self, argv: list[str], cwd: Path, timeout: float) -> Captured:
        try:
            done = subprocess.run(
                argv, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
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


@dataclass
class GitState:
    """Where this folder is in git: how many commits, which one, and whether it has uncommitted changes."""

    count: int
    commit: str
    dirty: bool
    branch: str = "unknown"

    @property
    def stamp(self) -> str:
        """What is recorded on the image. The build includes uncommitted changes, so it says so."""
        return self.commit + ("-dirty" if self.dirty else "")


def describe_stamp(stamp: str, branch: str = "") -> str:
    if not stamp or stamp in ("unknown", "<no value>"):
        return "unknown (built before commit stamps, or without git)"
    where = f" on {branch}" if branch and branch not in ("unknown", "<no value>") else ""
    if stamp.endswith("-dirty"):
        return f"commit {stamp[:-6]}{where} plus uncommitted changes"
    return f"commit {stamp}{where}"


@dataclass
class Options:
    command: str = ""
    version: str = ""
    skip_checks: bool = False
    keep_all: bool = False
    dry_run: bool = False
    assume_yes: bool = False


@dataclass
class Manager:
    options: Options
    shell: SystemShell = field(default_factory=SystemShell)
    root: Path = REPO_ROOT
    stdin: object = None
    environ: dict = field(default_factory=lambda: dict(os.environ))

    # -- output and control ----------------------------------------------------------------------

    def die(self, message: str, code: int = 1) -> None:
        print(f"ERROR: {message}", file=sys.stderr)
        raise Exit(code)

    def run(self, argv: list[str], *, cwd: Path | None = None, tolerate: bool = False) -> int:
        """Print a command, then run it unless this is a dry run. A failure stops the program."""
        where = cwd or self.root
        suffix = f"   (in {where.relative_to(self.root).as_posix()})" if where != self.root else ""
        print("+ " + " ".join(argv) + suffix, flush=True)
        if self.options.dry_run:
            return 0
        code = self.shell.run(argv, where)
        if code != 0 and not tolerate:
            raise Exit(code)
        return code

    def dry_run_notice(self) -> bool:
        """After the real commands: in a dry run nothing happened, so say that instead of reporting success."""
        if not self.options.dry_run:
            return False
        print()
        print("Dry run: nothing was changed.")
        return True

    def confirm(self) -> None:
        if self.options.assume_yes or self.options.dry_run:
            return
        print("Proceed? [y/N] ", end="", flush=True)
        stream = self.stdin if self.stdin is not None else sys.stdin
        answer = stream.readline().strip()
        if answer not in ("y", "Y", "yes", "YES"):
            print("Aborted; nothing was changed.")
            raise Exit(1)

    # -- reading Docker's state (always real, even in a dry run: these never change anything) ---

    def docker_read(self, args: list[str], timeout: float = DOCKER_READ_SECONDS) -> Captured:
        return self.shell.capture(["docker", *args], self.root, timeout)

    def our_extension(self) -> dict | None:
        """The `docker extension ls` entry for this extension, or None when it is not installed."""
        text = self.docker_read(["extension", "ls", "--format", "json"]).out.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
            entries = parsed if isinstance(parsed, list) else [parsed]
        except ValueError:  # older Docker prints one JSON object per line
            entries = []
            for line in text.splitlines():
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get("image", "")).startswith(f"{IMAGE}:"):
                return entry
        return None

    def installed_version(self) -> str:
        entry = self.our_extension()
        if not entry:
            return ""
        version = str(entry["image"]).split(":", 1)[1]
        return version if re.fullmatch(r"[0-9][0-9.]*", version) else ""

    def installed_state(self) -> str:
        entry = self.our_extension() or {}
        vm = entry.get("vm")
        return str(vm.get("status", "")) if isinstance(vm, dict) else ""

    def local_tags(self) -> list[str]:
        """Plain X.Y.Z tags only, oldest to newest. Tags such as 0.1.5-qs belong to someone's experiment."""
        out = self.docker_read(["images", IMAGE, "--format", "{{.Tag}}"]).out
        tags = {line.strip() for line in out.splitlines() if VERSION_PATTERN.match(line.strip())}
        return sorted(tags, key=version_key)

    def next_version(self) -> str:
        """The next version nobody has used: above both the installed version and every local tag.

        Docker keeps running the old image when a tag is reused, so a reused tag would look like a
        successful update that did nothing.
        """
        known = [v for v in [self.installed_version(), *self.local_tags()] if v]
        return bump_patch(max(known, key=version_key)) if known else "0.1.0"

    def git_state(self) -> GitState | None:
        """None when git is missing or this folder is not a repository: versions then count locally."""
        if not self.shell.which("git"):
            return None

        def read(args: list[str]) -> str | None:
            answer = self.shell.capture(["git", *args], self.root, GIT_READ_SECONDS)
            return answer.out.strip() if answer.code == 0 else None

        count, commit, changes = read(["rev-list", "--count", "HEAD"]), read(["rev-parse", "--short=7", "HEAD"]), read(["status", "--porcelain"])
        if count is None or not count.isdigit() or not commit or changes is None:
            return None
        # "HEAD" is what git prints when no branch is checked out (a detached checkout).
        branch = read(["rev-parse", "--abbrev-ref", "HEAD"]) or "unknown"
        return GitState(int(count), commit, bool(changes), "detached" if branch == "HEAD" else branch)

    def choose_version(self) -> tuple[str, GitState | None]:
        """The version to build, and the git state it was taken from.

        A clean checkout gets BASE_VERSION.<commit count> when nobody has used it here yet, so the number
        says which code it is and agrees on every machine. Anything else (uncommitted changes, a number
        already taken, no git) gets the next number nobody has used, because Docker keeps running the old
        image when a tag is reused. The image's commit stamp says what is really inside either way.
        """
        git = self.git_state()
        if self.options.version:
            return self.options.version, git
        if git is None:
            return self.next_version(), None
        known = {v for v in [self.installed_version(), *self.local_tags()] if v}
        preferred = f"{BASE_VERSION}.{git.count}"
        if not git.dirty and preferred not in known:
            return preferred, git
        return self.next_version(), git

    def image_label(self, version: str, label: str) -> str:
        template = '{{ index .Config.Labels "' + label + '" }}'
        answer = self.docker_read(["image", "inspect", "--format", template, f"{IMAGE}:{version}"])
        return answer.out.strip() if answer.code == 0 else ""

    def image_stamp(self, version: str) -> str:
        """The commit stamp recorded on a local image, or "" when it has none."""
        return self.image_label(version, COMMIT_LABEL)

    def image_branch(self, version: str) -> str:
        """The branch recorded on a local image, or "" when it has none."""
        return self.image_label(version, BRANCH_LABEL)

    # -- preflight ---------------------------------------------------------------------------------

    def preflight_docker(self) -> None:
        """Fail fast and clearly when Docker does not answer; warn when its VM is short of memory."""
        answer = self.docker_read(["info", "--format", "{{.MemTotal}}"], DOCKER_ANSWER_SECONDS)
        if answer.code is None:
            self.die(
                f"Docker did not answer within {DOCKER_ANSWER_SECONDS} seconds. Its virtual machine may be out of "
                "memory: close other applications or restart Docker Desktop, then try again."
            )
        if answer.code != 0:
            self.die("Docker is not running. Start Docker Desktop and try again.")
        try:
            have_gb = int(answer.out.strip()) / (1024 ** 3)
        except ValueError:
            return  # unknown stays unknown: no warning rather than a guess
        want_gb = float(self.environ.get("RESILIENCE_MIN_DOCKER_MEMORY_GB", DEFAULT_MIN_DOCKER_MEMORY_GB))
        if have_gb < want_gb:
            print(
                f"WARNING: Docker has {have_gb:.1f} GB of memory and building the extension works best with at "
                f"least {want_gb:g} GB. Raise it in Docker Desktop (Settings > Resources) or close other "
                "applications first; the build may be slow.",
                file=sys.stderr,
            )

    def python_for_tests(self) -> str:
        for candidate in (self.root / ".venv" / "bin" / "python", self.root / ".venv" / "Scripts" / "python.exe"):
            if candidate.is_file():
                return str(candidate)
        return sys.executable or "python3"

    def missing_check_tools(self) -> list[str]:
        needed = {"go": "Go (https://go.dev/dl/)", "node": "Node.js (https://nodejs.org/)"}
        return [label for tool, label in needed.items() if not self.shell.which(tool)]

    # -- commands -----------------------------------------------------------------------------------

    def cmd_check(self) -> None:
        missing = self.missing_check_tools()
        if missing:
            summary = "The test suites that guard the extension need " + " and ".join(missing) + " on your PATH."
            if self.options.dry_run:
                print(f"Note: {summary} A real run would stop here.", file=sys.stderr)
            else:
                self.die(f"{summary} Install them, or add --skip-checks to build without running the tests.")
        self.run([self.python_for_tests(), "-m", "pytest", "-q"])
        self.run(["go", "test", "./...", "-count=1"], cwd=self.root / "extension" / "helper")
        node_tests = sorted(
            Path(p).relative_to(self.root).as_posix()
            for p in glob.glob(str(self.root / "extension" / "ui-src" / "*.test.mjs"))
        )
        self.run(["node", "--test", *node_tests, "tests/frontend/connection-state.test.mjs", "tests/frontend/api.test.mjs"])

    def build_image(self, version: str, git: GitState | None = None) -> None:
        if not self.options.skip_checks:
            self.cmd_check()
        stamp = ["--build-arg", f"GIT_COMMIT={git.stamp}", "--build-arg", f"GIT_BRANCH={git.branch}"] if git else []
        self.run(["docker", "build", "-f", DOCKERFILE, *stamp, "-t", f"{IMAGE}:{version}", "."])

    def cmd_install(self) -> None:
        current = self.installed_version()
        if current:
            self.die(f"The extension is already installed (version {current}). Use 'update' instead.")
        version, git = self.choose_version()
        self.build_image(version, git)
        self.run(["docker", "extension", "install", "-f", f"{IMAGE}:{version}"])
        if self.dry_run_notice():
            return
        print()
        print(f"Installed {IMAGE}:{version}. Open Resilience Engine in Docker Desktop, choose Connect cluster, and")
        print("use Set up access to approve read-only access to your kind cluster.")

    def prune_old_versions(self, keep: set[str]) -> None:
        """Delete this extension's older plain X.Y.Z images, keeping the ones in ``keep``.

        Called only after the update itself succeeded, so a failed build or a version that does not start
        never costs you the images you could roll back to. Tags with a suffix (0.1.5-qs) are someone's
        experiment and are never touched, and Docker refuses to remove an image that is in use.
        """
        if self.options.keep_all:
            return
        old = [tag for tag in self.local_tags() if tag not in keep]
        if not old:
            return
        print(f"Removing {len(old)} older local image(s); keeping {', '.join(sorted(keep, key=version_key))} "
              "(use --keep-all to keep every image).")
        for tag in old:
            if self.run(["docker", "image", "rm", f"{IMAGE}:{tag}"], tolerate=True) != 0:
                print(f"  skipped {IMAGE}:{tag} (it may be in use)")

    def cmd_update(self) -> None:
        current = self.installed_version()
        if not current:
            self.die("The extension is not installed. Use 'install' first.")
        version, git = self.choose_version()
        if (not self.options.version and git is not None and not git.dirty
                and current == f"{BASE_VERSION}.{git.count}" and self.image_stamp(current) == git.stamp):
            print(f"Version {current} was built from this commit ({git.commit}) and is already installed.")
            print("Nothing to update. Pull new commits or change something first.")
            return
        if version == current or version in self.local_tags():
            self.die(f"Version {version} already exists locally. Docker would keep running the old image, so pick a new version.")
        self.build_image(version, git)
        self.run(["docker", "extension", "update", "-f", f"{IMAGE}:{version}"])
        # The new version and the one it replaced stay (a rollback is one command); everything older goes.
        self.prune_old_versions({version, current})
        if self.dry_run_notice():
            return
        print()
        print(f"Updated {current} -> {version}. Reopen the extension in Docker Desktop. Your data volume was kept.")
        print("If the role in the cluster changed, the Connect cluster page asks you to approve it once.")

    def helper_path(self) -> Path | None:
        """Where Docker Desktop on macOS keeps the host helper, if it is there."""
        home = self.environ.get("HOME")
        if not home:
            return None
        path = Path(home) / "Library/Containers/com.docker.docker/Data/extensions" / EXTENSION_ID / "host" / "resilience-helper"
        return path if path.is_file() else None

    def cmd_uninstall(self) -> None:
        current = self.installed_version()
        if not current:
            print("The extension is not installed; nothing to uninstall.")
            return
        print(f"This will remove the Resilience Engine extension (version {current}) from Docker Desktop.")
        self.confirm()
        # The background renewal job (a macOS LaunchAgent) runs the helper binary, which goes away with the
        # extension, so remove the job first. A failure here must not stop the uninstall.
        helper = self.helper_path()
        if helper is not None:
            self.run([str(helper), "agent-disable"], tolerate=True)
        self.run(["docker", "extension", "uninstall", EXTENSION_ID])
        if self.dry_run_notice():
            return
        print()
        print("Your cluster access was not touched. To remove it from the cluster too, run:")
        print("  ./scripts/extension-cluster-access.sh --revoke")

    def cmd_status(self) -> None:
        current = self.installed_version()
        if current:
            state = self.installed_state()
            print(f"Extension : installed, version {current}" + (f" ({state})" if state else ""))
            installed_stamp = self.image_stamp(current)
            print(f"Built from: {describe_stamp(installed_stamp, self.image_branch(current))}")
            git = self.git_state()
            if git is not None:
                print(f"This folder: {describe_stamp(git.stamp, git.branch)}")
                if installed_stamp == git.stamp and not git.dirty:
                    print("            The installed extension matches this folder.")
                else:
                    print("            It differs from the installed extension: run 'update' to install this folder.")
        else:
            print("Extension : not installed")
        print(f"Local image tags for {IMAGE}:")
        tags = self.local_tags()
        if not tags:
            print("  (none)")
        for tag in tags:
            print(f"  {tag}")

    def cmd_clean(self) -> None:
        keep = self.installed_version()
        if not keep:
            tags = self.local_tags()
            keep = tags[-1] if tags else ""
        old = [tag for tag in self.local_tags() if tag != keep]
        if not old:
            print("Nothing to clean.")
            return
        print(f"Keeping {IMAGE}:{keep or 'none'}. These local images will be deleted:")
        for tag in old:
            print(f"  {IMAGE}:{tag}")
        self.confirm()
        for tag in old:
            if self.run(["docker", "image", "rm", f"{IMAGE}:{tag}"], tolerate=True) != 0:
                print(f"  skipped {IMAGE}:{tag} (it may be in use)")


def version_key(version: str) -> VersionKey:
    return tuple(int(part) for part in version.split("."))


def bump_patch(version: str) -> str:
    major, minor, patch = version.split(".")[:3]
    return f"{major}.{minor}.{int(patch) + 1}"


def usage(stream) -> None:
    print("Usage: scripts/extension-manage.sh <command> [options]", file=stream)
    print("       scripts\\extension-manage.cmd <command> [options]   (Windows)", file=stream)
    print(file=stream)
    print((__doc__ or "").strip("\n"), file=stream)


def parse(argv: list[str]) -> Options:
    options = Options()
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("-h", "--help", "help"):
            usage(sys.stdout)
            raise Exit(0)
        if arg == "--version":
            value = argv[index + 1] if index + 1 < len(argv) else ""
            if not value:
                print("ERROR: --version needs a value such as 0.2.0", file=sys.stderr)
                raise Exit(2)
            options.version = value
            index += 2
            continue
        if arg == "--skip-checks":
            options.skip_checks = True
        elif arg == "--dry-run":
            options.dry_run = True
        elif arg == "--keep-all":
            options.keep_all = True
        elif arg in ("--yes", "-y"):
            options.assume_yes = True
        elif arg.startswith("-"):
            print(f"Unknown option: {arg}\n", file=sys.stderr)
            usage(sys.stderr)
            raise Exit(2)
        else:
            if options.command:
                print(f"ERROR: Only one command at a time (got '{options.command}' and '{arg}').", file=sys.stderr)
                raise Exit(2)
            options.command = arg
        index += 1

    if not options.command:
        usage(sys.stderr)
        raise Exit(2)
    if options.command not in COMMANDS:
        print(f"Unknown command: {options.command}\n", file=sys.stderr)
        usage(sys.stderr)
        raise Exit(2)
    if options.version and not VERSION_PATTERN.match(options.version):
        print(f"ERROR: --version must look like X.Y.Z (for example 0.2.0), got '{options.version}'.", file=sys.stderr)
        raise Exit(2)
    return options


def main(argv: list[str] | None = None, *, shell: SystemShell | None = None, root: Path = REPO_ROOT,
         stdin=None, environ: dict | None = None) -> int:
    try:
        manager = Manager(parse(sys.argv[1:] if argv is None else list(argv)), shell or SystemShell(), root, stdin,
                          dict(os.environ) if environ is None else environ)
        if manager.options.command != "check":
            if not manager.shell.which("docker"):
                manager.die("'docker' is not installed or not on PATH.")
            manager.preflight_docker()
        getattr(manager, f"cmd_{manager.options.command}")()
    except Exit as stop:
        return stop.code
    except KeyboardInterrupt:
        print("\nInterrupted; stopping.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
