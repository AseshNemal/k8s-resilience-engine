"""scripts/extension-manage.sh drives the Docker extension without needing Docker in the test.

A stand-in `docker` on PATH records every call, so the tests check what the script would really run.
The launcher is bash and starts scripts/extension_manage.py, so this is the end-to-end check of both on
macOS and Linux. tests/test_extension_manage_py.py covers the same program in-process on every OS.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="drives the bash launcher with a bash stand-in for docker")

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "extension-manage.sh"
IMAGE = "k8s-resilience-engine"


def installed_json(version: str) -> str:
    return (f'[{{"name":"K8s Resilience Engine","image":"{IMAGE}:{version}",'
            '"vm":{"status":"Running(1)"}}]')


@pytest.fixture
def fake_docker(tmp_path):
    log = tmp_path / "docker.log"
    docker = tmp_path / "bin" / "docker"
    docker.parent.mkdir()
    docker.write_text(
        '#!/usr/bin/env bash\n'
        'echo "docker $* (cwd=$PWD)" >> "$FAKE_DOCKER_LOG"\n'
        'case "$1 $2" in\n'
        '  "extension ls") echo "${FAKE_EXTENSIONS_JSON:-[]}" ;;\n'
        f'  "images {IMAGE}") printf "%s\\n" ${{FAKE_TAGS:-}} ;;\n'
        'esac\n'
    )
    docker.chmod(0o755)
    return log


def run(fake_docker, *args, installed=None, tags="", stdin="", path=None):
    env = {
        "PATH": path or f"{fake_docker.parent / 'bin'}:/usr/bin:/bin",
        "FAKE_DOCKER_LOG": str(fake_docker),
        "FAKE_TAGS": tags,
        "FAKE_EXTENSIONS_JSON": installed_json(installed) if installed else "[]",
        "HOME": str(fake_docker.parent),
    }
    result = subprocess.run(["bash", str(SCRIPT), *args], input=stdin, capture_output=True, text=True,
                            env=env, timeout=60)
    calls = fake_docker.read_text().splitlines() if fake_docker.exists() else []
    return result, calls


def only(calls, prefix):
    return [c for c in calls if c.startswith(prefix)]


def test_script_is_valid_bash_and_executable():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    assert os.access(SCRIPT, os.X_OK)


def test_help_lists_every_command():
    result = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)

    assert result.returncode == 0
    for command in ("install", "update", "uninstall", "status", "check", "clean"):
        assert command in result.stdout


def test_unknown_command_is_refused_with_usage(fake_docker):
    result, calls = run(fake_docker, "explode")

    assert result.returncode == 2
    assert "usage" in result.stderr.lower()
    assert only(calls, "docker build") == []


def test_missing_docker_is_reported(fake_docker):
    result, _ = run(fake_docker, "status", path="/usr/bin:/bin")

    assert result.returncode == 1
    assert "docker" in result.stderr.lower()


@pytest.mark.parametrize("bad", ["abc", "1.2", "1.2.3.4", "v1.2.3"])
def test_a_malformed_version_is_refused_before_anything_runs(fake_docker, bad):
    result, calls = run(fake_docker, "install", "--version", bad, "--skip-checks", "--yes")

    assert result.returncode == 2
    assert "X.Y.Z" in result.stderr
    assert only(calls, "docker build") == []


def test_install_builds_from_the_repo_root_then_installs(fake_docker):
    result, calls = run(fake_docker, "install", "--version", "1.2.3", "--skip-checks", "--yes")

    assert result.returncode == 0, result.stderr
    build = only(calls, "docker build")
    install = only(calls, "docker extension install")
    assert build and f"-t {IMAGE}:1.2.3" in build[0] and "-f extension/Dockerfile" in build[0]
    assert f"(cwd={REPO.resolve()})" in build[0] or f"(cwd={REPO})" in build[0]
    assert install == [f"docker extension install -f {IMAGE}:1.2.3 (cwd={build[0].split('cwd=')[1]}"]
    assert calls.index(build[0]) < calls.index(install[0])


def test_install_refuses_when_already_installed(fake_docker):
    result, calls = run(fake_docker, "install", "--skip-checks", "--yes", installed="0.1.8")

    assert result.returncode == 1
    assert "update" in result.stderr
    assert only(calls, "docker build") == []


def test_update_builds_then_updates_to_the_given_version(fake_docker):
    result, calls = run(fake_docker, "update", "--version", "1.2.3", "--skip-checks", "--yes", installed="0.1.8")

    assert result.returncode == 0, result.stderr
    build = only(calls, "docker build")
    update = only(calls, "docker extension update")
    assert len(build) == 1 and len(update) == 1
    assert update[0].startswith(f"docker extension update -f {IMAGE}:1.2.3")
    assert calls.index(build[0]) < calls.index(update[0])


def test_update_bumps_the_patch_of_the_installed_version(fake_docker):
    result, calls = run(fake_docker, "update", "--skip-checks", "--yes", installed="0.4.9", tags="0.4.9")

    assert result.returncode == 0, result.stderr
    assert f"-t {IMAGE}:0.4.10" in only(calls, "docker build")[0]


def test_update_never_reuses_a_tag_that_exists_locally_and_sorts_numerically(fake_docker):
    result, calls = run(fake_docker, "update", "--skip-checks", "--yes", installed="0.4.9", tags="0.4.9 0.4.10 0.4.7")

    assert result.returncode == 0, result.stderr
    assert f"-t {IMAGE}:0.4.11" in only(calls, "docker build")[0]


def test_update_without_an_installed_extension_points_at_install(fake_docker):
    result, calls = run(fake_docker, "update", "--skip-checks", "--yes")

    assert result.returncode == 1
    assert "install" in result.stderr
    assert only(calls, "docker build") == []


def test_dry_run_prints_the_commands_and_runs_none_of_them(fake_docker):
    result, calls = run(fake_docker, "update", "--dry-run", "--version", "1.2.3", "--skip-checks", "--yes",
                        installed="0.1.8")

    assert result.returncode == 0, result.stderr
    # The commit stamp (--build-arg GIT_COMMIT=...) appears when this checkout is a git repository.
    assert "docker build -f extension/Dockerfile" in result.stdout
    assert f"-t {IMAGE}:1.2.3 ." in result.stdout
    assert f"docker extension update -f {IMAGE}:1.2.3" in result.stdout
    assert only(calls, "docker build") == [] and only(calls, "docker extension update") == []


def test_checks_run_python_go_and_node_suites(fake_docker):
    result, _ = run(fake_docker, "check", "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "pytest" in result.stdout
    assert "go test" in result.stdout
    assert "node --test" in result.stdout


def test_update_runs_the_checks_first_unless_skipped(fake_docker):
    checked, _ = run(fake_docker, "update", "--dry-run", "--version", "1.2.3", "--yes", installed="0.1.8")
    skipped, _ = run(fake_docker, "update", "--dry-run", "--version", "1.2.3", "--yes", "--skip-checks",
                     installed="0.1.8")

    assert "pytest" in checked.stdout and checked.stdout.index("pytest") < checked.stdout.index("docker build")
    assert "pytest" not in skipped.stdout


def test_status_reports_the_installed_version_and_local_tags(fake_docker):
    result, _ = run(fake_docker, "status", installed="0.4.9", tags="0.4.9 0.4.7")

    assert result.returncode == 0, result.stderr
    assert "0.4.9" in result.stdout and "0.4.7" in result.stdout
    assert "Running" in result.stdout


def test_status_when_not_installed_says_so(fake_docker):
    result, _ = run(fake_docker, "status")

    assert result.returncode == 0
    assert "not installed" in result.stdout.lower()


def test_uninstall_asks_first_and_aborts_without_a_yes(fake_docker):
    result, calls = run(fake_docker, "uninstall", installed="0.1.8", stdin="")

    assert result.returncode == 1
    assert "Aborted" in result.stdout
    assert only(calls, "docker extension uninstall") == []


def test_uninstall_removes_the_extension_and_mentions_the_cluster_access(fake_docker):
    result, calls = run(fake_docker, "uninstall", "--yes", installed="0.1.8")

    assert result.returncode == 0, result.stderr
    assert only(calls, f"docker extension uninstall {IMAGE}")
    assert "extension-cluster-access.sh --revoke" in result.stdout


def install_fake_helper(fake_docker, exit_code=0):
    """Put a stand-in for the host helper where Docker Desktop keeps it, logging into the docker log."""
    helper = (fake_docker.parent / "Library/Containers/com.docker.docker/Data/extensions" / IMAGE / "host" / "resilience-helper")
    helper.parent.mkdir(parents=True)
    helper.write_text(f'#!/usr/bin/env bash\necho "helper $*" >> "$FAKE_DOCKER_LOG"\nexit {exit_code}\n')
    helper.chmod(0o755)


def test_uninstall_removes_the_background_renewal_job_before_the_extension_and_its_helper_go(fake_docker):
    install_fake_helper(fake_docker)
    result, calls = run(fake_docker, "uninstall", "--yes", installed="0.1.8")

    assert result.returncode == 0, result.stderr
    kinds = [c.split(" ")[0] + " " + c.split(" ")[1] for c in calls if c.startswith(("helper", "docker extension uninstall"))]
    assert kinds == ["helper agent-disable", "docker extension"], calls


def test_a_helper_that_fails_never_blocks_the_uninstall(fake_docker):
    install_fake_helper(fake_docker, exit_code=1)
    result, calls = run(fake_docker, "uninstall", "--yes", installed="0.1.8")

    assert result.returncode == 0, result.stderr
    assert only(calls, f"docker extension uninstall {IMAGE}")


def test_uninstall_without_a_helper_on_disk_just_uninstalls(fake_docker):
    result, calls = run(fake_docker, "uninstall", "--yes", installed="0.1.8")

    assert result.returncode == 0, result.stderr
    assert not only(calls, "helper")


def test_a_dry_run_uninstall_removes_nothing(fake_docker):
    install_fake_helper(fake_docker)
    result, calls = run(fake_docker, "uninstall", "--dry-run", installed="0.1.8")

    assert result.returncode == 0, result.stderr
    assert not only(calls, "helper") and not only(calls, "docker extension uninstall")
    assert "agent-disable" in result.stdout


def test_uninstall_when_not_installed_changes_nothing(fake_docker):
    result, calls = run(fake_docker, "uninstall", "--yes")

    assert result.returncode == 0
    assert only(calls, "docker extension uninstall") == []


def test_clean_removes_old_tags_but_never_the_installed_one_or_other_images(fake_docker):
    result, calls = run(fake_docker, "clean", "--yes", installed="0.1.8", tags="0.1.8 0.1.7 0.1.6")

    assert result.returncode == 0, result.stderr
    removed = only(calls, "docker image rm")
    assert {c.split(" (")[0] for c in removed} == {
        f"docker image rm {IMAGE}:0.1.7", f"docker image rm {IMAGE}:0.1.6"}


def test_clean_dry_run_lists_but_does_not_remove(fake_docker):
    result, calls = run(fake_docker, "clean", "--dry-run", "--yes", installed="0.1.8", tags="0.1.8 0.1.7")

    assert f"{IMAGE}:0.1.7" in result.stdout
    assert only(calls, "docker image rm") == []


def test_clean_with_nothing_old_says_so(fake_docker):
    result, calls = run(fake_docker, "clean", "--yes", installed="0.1.8", tags="0.1.8")

    assert result.returncode == 0
    assert "nothing to clean" in result.stdout.lower()
    assert only(calls, "docker image rm") == []


@pytest.mark.parametrize("version", ["0.1.8", "0.1.7"])
def test_update_refuses_a_version_that_already_exists_because_docker_would_keep_the_old_image(fake_docker, version):
    result, calls = run(fake_docker, "update", "--version", version, "--skip-checks", "--yes",
                        installed="0.1.8", tags="0.1.8 0.1.7")

    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert only(calls, "docker build") == []


@pytest.mark.parametrize(("command", "installed"), [("install", None), ("update", "0.1.8"), ("uninstall", "0.1.8")])
def test_dry_run_never_claims_it_changed_anything(fake_docker, command, installed):
    result, _ = run(fake_docker, command, "--dry-run", "--skip-checks", "--yes", installed=installed)

    assert result.returncode == 0, result.stderr
    assert "Dry run" in result.stdout
    for claim in ("Installed ", "Updated ", "Your cluster access was not touched"):
        assert claim not in result.stdout
