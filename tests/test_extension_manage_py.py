"""scripts/extension_manage.py, exercised in-process with a recording stand-in for the shell.

No Docker, Go or Node is needed, and nothing here is POSIX-specific, so these run on macOS, Linux and
Windows. tests/test_extension_manage_script.py covers the same program end to end through the bash
launcher with a fake `docker` on PATH (POSIX only).
"""

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("extension_manage", REPO / "scripts" / "extension_manage.py")
em = importlib.util.module_from_spec(_spec)
sys.modules["extension_manage"] = em
_spec.loader.exec_module(em)

IMAGE = em.IMAGE
GIB = 1024 ** 3


class FakeShell:
    """Answers reads from a table, records everything that would have run, never starts a process."""

    def __init__(self, tools=("docker", "go", "node"), installed=None, tags=(), memory=8 * GIB,
                 info=None, run_codes=None, git=None, stamps=None, branches=None):
        """``git`` is (commit count, short commit, has uncommitted changes[, branch]) or None for "no git here".
        ``stamps`` and ``branches`` map an image tag to the commit stamp and branch recorded on it."""
        self.tools = set(tools) | ({"git"} if git else set())
        self.stamps = stamps or {}
        self.branches = branches or {}
        self.ran = []  # (argv, cwd)
        self.reads = []
        self.run_codes = run_codes or {}
        extensions = []
        if installed:
            extensions = [{"name": "K8s Resilience Engine", "image": f"{IMAGE}:{installed}",
                           "vm": {"status": "Running(1)"}}]
        self.answers = {
            "info": info if info is not None else em.Captured(0, str(memory)),
            "extension ls": em.Captured(0, json.dumps(extensions)),
            "images": em.Captured(0, "\n".join(tags)),
        }
        if git:
            count, commit, dirty, *rest = git
            self.branch = rest[0] if rest else "main"
            self.answers["rev-list"] = em.Captured(0, f"{count}\n")
            self.answers["rev-parse"] = em.Captured(0, f"{commit}\n")
            self.answers["status"] = em.Captured(0, " M app/main.py\n" if dirty else "")

    def which(self, name):
        return f"/fake/{name}" if name in self.tools else None

    def capture(self, argv, cwd, timeout):
        self.reads.append((argv, timeout))
        if argv[:2] == ["docker", "image"]:  # docker image inspect --format <label template> <image:tag>
            tag = argv[-1].split(":", 1)[1]
            wanted = self.branches if em.BRANCH_LABEL in " ".join(argv) else self.stamps
            return em.Captured(0, wanted.get(tag, ""))
        if argv[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return em.Captured(0, self.branch + "\n")
        key = argv[1] if argv[1] != "extension" else "extension ls"
        return self.answers[key]

    def run(self, argv, cwd):
        self.ran.append((argv, cwd))
        for prefix, code in self.run_codes.items():
            if " ".join(argv).startswith(prefix):
                return code
        return 0

    def commands(self, prefix=""):
        return [" ".join(argv) for argv, _ in self.ran if " ".join(argv).startswith(prefix)]


def run(shell, *args, root=REPO, stdin="", environ=None):
    return em.main(list(args), shell=shell, root=root, stdin=io.StringIO(stdin), environ=environ or {})


# -- Docker has to answer, and a small VM gets a warning, not a refusal ---------------------------

def test_a_docker_that_does_not_answer_is_reported_with_the_likely_cause(capsys):
    shell = FakeShell(info=em.Captured(None, ""))

    assert run(shell, "update", "--skip-checks", "--yes") == 1

    err = capsys.readouterr().err
    assert "did not answer" in err and "out of memory" in err
    assert shell.ran == []


def test_a_docker_that_is_not_running_says_so_instead_of_claiming_not_installed(capsys):
    shell = FakeShell(info=em.Captured(1, ""))

    assert run(shell, "status") == 1

    assert "not running" in capsys.readouterr().err
    assert shell.ran == []


def test_the_docker_probe_has_a_short_timeout_and_the_reads_a_longer_one():
    shell = FakeShell(installed="0.1.8")

    run(shell, "status")

    timeouts = {argv[1]: timeout for argv, timeout in shell.reads}
    assert timeouts["info"] == em.DOCKER_ANSWER_SECONDS
    assert timeouts["images"] == em.DOCKER_READ_SECONDS


def test_a_small_docker_vm_is_warned_about_but_the_build_still_runs(capsys):
    shell = FakeShell(installed="0.1.8", memory=2 * GIB)

    assert run(shell, "update", "--skip-checks", "--yes", "--version", "0.1.9") == 0

    assert "WARNING: Docker has 2.0 GB" in capsys.readouterr().err
    assert shell.commands("docker build")


@pytest.mark.parametrize("memory", [4 * GIB, 3 * GIB, 16 * GIB])
def test_enough_memory_prints_no_warning(capsys, memory):
    run(FakeShell(memory=memory), "status")

    assert "WARNING" not in capsys.readouterr().err


def test_an_unreadable_memory_figure_prints_no_warning_instead_of_a_guess(capsys):
    run(FakeShell(info=em.Captured(0, "")), "status")

    assert "WARNING" not in capsys.readouterr().err


def test_the_memory_threshold_is_configurable(capsys):
    run(FakeShell(memory=4 * GIB), "status", environ={"RESILIENCE_MIN_DOCKER_MEMORY_GB": "6"})

    assert "WARNING: Docker has 4.0 GB" in capsys.readouterr().err


def test_check_does_not_need_docker():
    shell = FakeShell(tools=("go", "node"))

    assert run(shell, "check", "--dry-run") == 0
    assert shell.reads == []


def test_every_other_command_needs_docker_on_the_path(capsys):
    assert run(FakeShell(tools=()), "status") == 1
    assert "docker" in capsys.readouterr().err


# -- check: missing tools are listed together and never silently skipped ------------------------

def test_check_lists_every_missing_tool_and_runs_nothing(capsys):
    shell = FakeShell(tools=())

    assert run(shell, "check") == 1

    err = capsys.readouterr().err
    assert "Go" in err and "Node.js" in err and "--skip-checks" in err
    assert shell.ran == []


def test_check_names_only_the_tool_that_is_missing(capsys):
    shell = FakeShell(tools=("go",))

    assert run(shell, "check") == 1

    err = capsys.readouterr().err
    assert "Node.js" in err and "Go (" not in err


def test_an_update_stops_before_building_when_a_test_tool_is_missing(capsys):
    shell = FakeShell(tools=("docker",), installed="0.1.8")

    assert run(shell, "update", "--yes") == 1

    assert shell.commands("docker build") == []
    assert "--skip-checks" in capsys.readouterr().err


def test_skipping_checks_does_not_require_go_or_node():
    shell = FakeShell(tools=("docker",), installed="0.1.8")

    assert run(shell, "update", "--yes", "--skip-checks", "--version", "0.1.9") == 0
    assert shell.commands("docker build")


def test_a_dry_run_of_check_notes_the_missing_tool_and_still_succeeds(capsys):
    shell = FakeShell(tools=())

    assert run(shell, "check", "--dry-run") == 0

    assert "A real run would stop here" in capsys.readouterr().err
    assert shell.ran == []


def test_check_runs_the_three_suites_with_go_in_the_helper_folder(tmp_path):
    (tmp_path / "extension" / "helper").mkdir(parents=True)
    ui = tmp_path / "extension" / "ui-src"
    ui.mkdir()
    for name in ("shim.test.mjs", "autoconnect.test.mjs", "chrome.js"):
        (ui / name).write_text("")
    shell = FakeShell()

    assert run(shell, "check", root=tmp_path) == 0

    (pytest_argv, _), (go_argv, go_cwd), (node_argv, node_cwd) = shell.ran
    assert pytest_argv[1:] == ["-m", "pytest", "-q"]
    assert go_argv == ["go", "test", "./...", "-count=1"]
    assert go_cwd == tmp_path / "extension" / "helper"
    assert node_cwd == tmp_path
    assert node_argv == [
        "node", "--test",
        "extension/ui-src/autoconnect.test.mjs", "extension/ui-src/shim.test.mjs",
        "tests/frontend/connection-state.test.mjs", "tests/frontend/api.test.mjs",
    ]


@pytest.mark.parametrize("relative", [(".venv", "bin", "python"), (".venv", "Scripts", "python.exe")])
def test_check_prefers_the_virtualenv_python_on_either_layout(tmp_path, relative):
    interpreter = tmp_path.joinpath(*relative)
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("")
    manager = em.Manager(em.Options(), FakeShell(), tmp_path)

    assert manager.python_for_tests() == str(interpreter)


def test_check_falls_back_to_the_interpreter_running_the_script(tmp_path):
    assert em.Manager(em.Options(), FakeShell(), tmp_path).python_for_tests() == sys.executable


def test_a_failing_suite_stops_the_update_with_that_exit_code():
    shell = FakeShell(installed="0.1.8", run_codes={"go test": 7})

    assert run(shell, "update", "--yes") == 7

    assert shell.commands("docker build") == [] and shell.commands("docker extension") == []
    assert shell.commands("node") == []


def test_a_failing_build_never_reaches_the_extension_update():
    shell = FakeShell(installed="0.1.8", run_codes={"docker build": 3})

    assert run(shell, "update", "--skip-checks", "--yes") == 3
    assert shell.commands("docker extension") == []


# -- reading what Docker reports ------------------------------------------------------------------

def test_one_json_object_per_line_is_understood(capsys):
    shell = FakeShell()
    shell.answers["extension ls"] = em.Captured(0, "\n".join([
        json.dumps({"image": "someone/else:1.0.0"}),
        json.dumps({"image": f"{IMAGE}:0.4.2", "vm": {"status": "Stopped"}}),
    ]))

    run(shell, "status")

    assert "installed, version 0.4.2 (Stopped)" in capsys.readouterr().out


def test_another_extension_is_never_mistaken_for_this_one(capsys):
    shell = FakeShell()
    shell.answers["extension ls"] = em.Captured(0, json.dumps([{"image": "docker/logs-explorer:0.2.5"}]))

    run(shell, "status")

    assert "not installed" in capsys.readouterr().out


def test_unparseable_output_counts_as_not_installed(capsys):
    shell = FakeShell()
    shell.answers["extension ls"] = em.Captured(0, "<<not json>>")

    run(shell, "status")

    assert "not installed" in capsys.readouterr().out


def test_only_plain_version_tags_are_managed_and_they_sort_numerically(capsys):
    shell = FakeShell(tags=["0.1.10", "0.1.9", "0.1.5-qs", "latest", "0.1.2", ""])

    run(shell, "status")

    lines = [line.strip() for line in capsys.readouterr().out.splitlines()[2:]]
    assert lines == ["0.1.2", "0.1.9", "0.1.10"]


def test_next_version_passes_ten_instead_of_sorting_it_as_text():
    shell = FakeShell(installed="0.1.9", tags=["0.1.9", "0.1.10"])

    assert run(shell, "update", "--skip-checks", "--yes") == 0
    assert shell.commands("docker build") == [f"docker build -f extension/Dockerfile -t {IMAGE}:0.1.11 ."]


def test_the_first_install_starts_at_0_1_0():
    shell = FakeShell()

    assert run(shell, "install", "--skip-checks", "--yes") == 0
    assert f"-t {IMAGE}:0.1.0" in shell.commands("docker build")[0]


def test_builds_and_installs_run_from_the_repository_root():
    shell = FakeShell()

    run(shell, "install", "--skip-checks", "--yes", "--version", "1.2.3")

    assert {cwd for _, cwd in shell.ran} == {REPO}


# -- the version follows git ------------------------------------------------------------------------

def built(shell):
    """The one `docker build` command, split into words."""
    (command,) = shell.commands("docker build")
    return command.split()


def test_a_clean_checkout_builds_the_version_its_commit_count_names():
    shell = FakeShell(installed="0.1.200", tags=["0.1.200"], git=(214, "16c62a1", False))

    assert run(shell, "update", "--skip-checks", "--yes") == 0

    words = built(shell)
    assert f"{IMAGE}:0.1.214" in words
    assert words[words.index("--build-arg") + 1] == "GIT_COMMIT=16c62a1"
    assert shell.commands("docker extension update") == [f"docker extension update -f {IMAGE}:0.1.214"]


def test_pulling_commits_moves_the_version_forward_by_itself():
    shell = FakeShell(installed="0.1.214", tags=["0.1.214"], git=(220, "9f3a1b2", False))

    run(shell, "update", "--skip-checks", "--yes")

    assert f"{IMAGE}:0.1.220" in built(shell)


def test_uncommitted_changes_get_the_next_unused_version_and_say_so_on_the_image():
    shell = FakeShell(installed="0.1.214", tags=["0.1.214"], git=(214, "16c62a1", True))

    run(shell, "update", "--skip-checks", "--yes")

    words = built(shell)
    assert f"{IMAGE}:0.1.215" in words
    assert words[words.index("--build-arg") + 1] == "GIT_COMMIT=16c62a1-dirty"


def test_every_local_edit_gets_a_fresh_version_so_docker_really_replaces_the_image():
    shell = FakeShell(installed="0.1.215", tags=["0.1.214", "0.1.215"], git=(214, "16c62a1", True))

    run(shell, "update", "--skip-checks", "--yes")

    assert f"{IMAGE}:0.1.216" in built(shell)


def test_committing_after_local_builds_falls_back_when_the_count_number_is_already_taken():
    # Two local builds used 0.1.215 and 0.1.216; committing makes the count 215, which is taken.
    shell = FakeShell(installed="0.1.216", tags=["0.1.214", "0.1.215", "0.1.216"], git=(215, "b7d41e0", False))

    run(shell, "update", "--skip-checks", "--yes")

    assert f"{IMAGE}:0.1.217" in built(shell)


def test_switching_to_a_branch_with_fewer_commits_may_install_a_lower_version():
    shell = FakeShell(installed="0.1.220", tags=["0.1.220"], git=(214, "16c62a1", False))

    assert run(shell, "update", "--skip-checks", "--yes") == 0

    assert f"{IMAGE}:0.1.214" in built(shell)


def test_the_same_commit_is_not_installed_twice(capsys):
    shell = FakeShell(installed="0.1.214", tags=["0.1.214"], git=(214, "16c62a1", False),
                      stamps={"0.1.214": "16c62a1"})

    assert run(shell, "update", "--skip-checks", "--yes") == 0

    assert "already installed" in capsys.readouterr().out
    assert shell.commands("docker build") == []


def test_an_installed_build_with_uncommitted_changes_is_replaced_once_they_are_committed():
    shell = FakeShell(installed="0.1.214", tags=["0.1.214"], git=(214, "16c62a1", False),
                      stamps={"0.1.214": "16c62a1-dirty"})

    run(shell, "update", "--skip-checks", "--yes")

    assert shell.commands("docker build")  # same number, different contents: a new tag is built


def test_the_same_number_with_different_contents_is_not_mistaken_for_up_to_date():
    # Same commit count, but the installed image came from another commit (another branch or a teammate's
    # work): the number alone says nothing, so it is rebuilt under the next unused tag.
    shell = FakeShell(installed="0.1.214", tags=["0.1.214"], git=(214, "16c62a1", False),
                      stamps={"0.1.214": "aaaaaaa"})

    run(shell, "update", "--skip-checks", "--yes")

    assert f"{IMAGE}:0.1.215" in built(shell)


def test_the_build_records_the_branch_next_to_the_commit():
    shell = FakeShell(git=(214, "16c62a1", False, "integration/team-sync"))

    run(shell, "install", "--skip-checks", "--yes")

    words = built(shell)
    assert "GIT_COMMIT=16c62a1" in words
    assert "GIT_BRANCH=integration/team-sync" in words


def test_a_detached_checkout_is_recorded_as_detached_not_as_head():
    shell = FakeShell(git=(214, "16c62a1", False, "HEAD"))

    run(shell, "install", "--skip-checks", "--yes")

    assert "GIT_BRANCH=detached" in built(shell)


def test_status_names_the_branch_so_two_builds_with_the_same_number_can_be_told_apart(capsys):
    shell = FakeShell(installed="0.1.214", tags=["0.1.214"], git=(214, "16c62a1", False, "feature/x"),
                      stamps={"0.1.214": "aaaaaaa"}, branches={"0.1.214": "integration/team-sync"})

    run(shell, "status")

    out = capsys.readouterr().out
    assert "Built from: commit aaaaaaa on integration/team-sync" in out
    assert "This folder: commit 16c62a1 on feature/x" in out
    assert "run 'update'" in out


def removed(shell):
    return shell.commands("docker image rm")


def test_an_update_keeps_the_new_and_the_previous_image_and_removes_the_older_ones(capsys):
    shell = FakeShell(installed="0.1.9", tags=["0.1.5", "0.1.7", "0.1.8", "0.1.9"])

    assert run(shell, "update", "--skip-checks", "--yes") == 0

    assert removed(shell) == [f"docker image rm {IMAGE}:0.1.5", f"docker image rm {IMAGE}:0.1.7",
                              f"docker image rm {IMAGE}:0.1.8"]
    assert "keeping 0.1.9, 0.1.10" in capsys.readouterr().out


def test_keep_all_leaves_every_image_alone():
    shell = FakeShell(installed="0.1.9", tags=["0.1.5", "0.1.7", "0.1.9"])

    run(shell, "update", "--skip-checks", "--yes", "--keep-all")

    assert removed(shell) == []


def test_a_failed_update_deletes_nothing():
    shell = FakeShell(installed="0.1.9", tags=["0.1.5", "0.1.7", "0.1.9"],
                      run_codes={"docker extension update": 1})

    assert run(shell, "update", "--skip-checks", "--yes") != 0

    assert removed(shell) == []


def test_a_failed_build_deletes_nothing():
    shell = FakeShell(installed="0.1.9", tags=["0.1.5", "0.1.9"], run_codes={"docker build": 1})

    assert run(shell, "update", "--skip-checks", "--yes") != 0

    assert removed(shell) == []


def test_an_install_deletes_nothing():
    shell = FakeShell(tags=["0.1.1", "0.1.2"])

    run(shell, "install", "--skip-checks", "--yes")

    assert removed(shell) == []


def test_images_that_are_in_use_are_skipped_with_a_note_and_the_update_still_succeeds(capsys):
    shell = FakeShell(installed="0.1.9", tags=["0.1.5", "0.1.9"], run_codes={"docker image rm": 1})

    assert run(shell, "update", "--skip-checks", "--yes") == 0

    assert f"skipped {IMAGE}:0.1.5" in capsys.readouterr().out


def test_experiment_tags_with_a_suffix_are_never_removed():
    shell = FakeShell(installed="0.1.9", tags=["0.1.5-qs", "0.1.9"])

    run(shell, "update", "--skip-checks", "--yes")

    assert removed(shell) == []


def test_a_dry_run_shows_what_would_be_removed_and_removes_nothing(capsys):
    shell = FakeShell(installed="0.1.9", tags=["0.1.5", "0.1.9"])

    run(shell, "update", "--skip-checks", "--dry-run")

    assert f"+ docker image rm {IMAGE}:0.1.5" in capsys.readouterr().out
    assert shell.commands("docker image rm") == []


def test_an_explicit_version_still_wins_and_is_still_stamped():
    shell = FakeShell(git=(214, "16c62a1", False))

    run(shell, "install", "--skip-checks", "--yes", "--version", "2.0.0")

    words = built(shell)
    assert f"{IMAGE}:2.0.0" in words
    assert "GIT_COMMIT=16c62a1" in words


def test_without_git_the_version_counts_locally_and_nothing_is_stamped():
    shell = FakeShell(installed="0.1.9", tags=["0.1.9"])

    run(shell, "update", "--skip-checks", "--yes")

    assert shell.commands("docker build") == [f"docker build -f extension/Dockerfile -t {IMAGE}:0.1.10 ."]


def test_status_says_what_the_installed_build_came_from_and_whether_it_matches_this_folder(capsys):
    shell = FakeShell(installed="0.1.214", tags=["0.1.214"], git=(214, "16c62a1", False),
                      stamps={"0.1.214": "16c62a1"})

    run(shell, "status")

    out = capsys.readouterr().out
    assert "Built from: commit 16c62a1" in out
    assert "The installed extension matches this folder." in out


def test_status_shows_when_the_folder_has_moved_on(capsys):
    shell = FakeShell(installed="0.1.214", tags=["0.1.214"], git=(220, "9f3a1b2", True),
                      stamps={"0.1.214": "16c62a1"})

    run(shell, "status")

    out = capsys.readouterr().out
    assert "commit 9f3a1b2 on main plus uncommitted changes" in out
    assert "run 'update'" in out


def test_an_image_without_a_stamp_is_reported_as_unknown_not_guessed(capsys):
    shell = FakeShell(installed="0.1.5", tags=["0.1.5"])

    run(shell, "status")

    assert "Built from: unknown" in capsys.readouterr().out


# -- confirmation, dry runs and usage ---------------------------------------------------------------

def test_clean_asks_first_and_an_empty_answer_aborts(capsys):
    shell = FakeShell(installed="0.1.8", tags=["0.1.8", "0.1.7"])

    assert run(shell, "clean", stdin="") == 1

    assert "Aborted" in capsys.readouterr().out
    assert shell.commands("docker image rm") == []


@pytest.mark.parametrize("answer", ["y\n", "Y\n", "yes\n", "YES\n"])
def test_clean_proceeds_on_a_yes(answer):
    shell = FakeShell(installed="0.1.8", tags=["0.1.8", "0.1.7"])

    assert run(shell, "clean", stdin=answer) == 0
    assert shell.commands("docker image rm") == [f"docker image rm {IMAGE}:0.1.7"]


@pytest.mark.parametrize("answer", ["n\n", "no\n", "maybe\n"])
def test_anything_but_a_yes_aborts(answer):
    shell = FakeShell(installed="0.1.8", tags=["0.1.8", "0.1.7"])

    assert run(shell, "clean", stdin=answer) == 1
    assert shell.commands("docker image rm") == []


def test_clean_reports_an_image_it_could_not_remove_and_carries_on(capsys):
    shell = FakeShell(installed="0.1.8", tags=["0.1.8", "0.1.7", "0.1.6"],
                      run_codes={f"docker image rm {IMAGE}:0.1.7": 1})

    assert run(shell, "clean", "--yes") == 0

    assert f"skipped {IMAGE}:0.1.7" in capsys.readouterr().out
    # Oldest first, and a failure on one image does not stop the next.
    assert shell.commands("docker image rm") == [f"docker image rm {IMAGE}:0.1.6", f"docker image rm {IMAGE}:0.1.7"]


def test_without_an_installed_extension_clean_keeps_the_newest_tag(capsys):
    shell = FakeShell(tags=["0.1.2", "0.1.10", "0.1.9"])

    run(shell, "clean", "--yes")

    assert f"Keeping {IMAGE}:0.1.10" in capsys.readouterr().out
    assert sorted(shell.commands("docker image rm")) == sorted(
        [f"docker image rm {IMAGE}:0.1.2", f"docker image rm {IMAGE}:0.1.9"])


def test_a_dry_run_runs_nothing_and_says_so(capsys):
    shell = FakeShell(installed="0.1.8")

    assert run(shell, "update", "--dry-run", "--yes", "--skip-checks", "--version", "1.2.3") == 0

    out = capsys.readouterr().out
    assert shell.ran == []
    assert f"+ docker build -f extension/Dockerfile -t {IMAGE}:1.2.3 ." in out
    assert "Dry run: nothing was changed." in out and "Updated " not in out


@pytest.mark.parametrize("argv", [
    [], ["explode"], ["status", "install"], ["status", "--wat"], ["status", "--version"],
    ["install", "--version", "1.2"], ["install", "--version", "v1.2.3"], ["install", "--version", "1.2.3.4"],
])
def test_usage_errors_exit_2_and_run_nothing(argv):
    shell = FakeShell()

    assert run(shell, *argv) == 2
    assert shell.ran == [] and shell.reads == []


def test_help_prints_every_command_to_stdout_and_exits_0(capsys):
    assert run(FakeShell(), "--help") == 0

    out = capsys.readouterr().out
    for command in em.COMMANDS:
        assert command in out
    assert "extension-manage.cmd" in out


def test_update_refuses_a_tag_that_exists_locally(capsys):
    shell = FakeShell(installed="0.1.8", tags=["0.1.8", "0.1.7"])

    assert run(shell, "update", "--version", "0.1.7", "--skip-checks", "--yes") == 1

    assert "already exists" in capsys.readouterr().err
    assert shell.commands("docker build") == []


def test_ctrl_c_stops_cleanly_with_the_conventional_exit_code(capsys):
    class Interrupted(FakeShell):
        def run(self, argv, cwd):
            raise KeyboardInterrupt

    assert run(Interrupted(installed="0.1.8"), "update", "--skip-checks", "--yes") == 130
    assert "Interrupted" in capsys.readouterr().err


def test_the_source_avoids_syntax_newer_than_python_3_9():
    import ast
    tree = ast.parse((REPO / "scripts" / "extension_manage.py").read_text(encoding="utf-8"))
    assert not [node for node in ast.walk(tree) if node.__class__.__name__ in ("Match", "TryStar")]
