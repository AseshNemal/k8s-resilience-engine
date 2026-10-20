"""extension/Dockerfile and .dockerignore keep the extension image small and the build light.

These are text checks on purpose: building the image needs Docker and several minutes, and the rules
below are the ones that were broken or missing when an update froze a 4 GB Docker Desktop VM.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "extension" / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = [
    line.strip()
    for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
]
REQUIREMENTS = (ROOT / "requirements.txt").read_text(encoding="utf-8")


def instructions():
    """Dockerfile instructions with line continuations joined and comments removed."""
    joined = re.sub(r"\\\n\s*", " ", DOCKERFILE)
    return [line.strip() for line in joined.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def stage_instructions(stage: str) -> list[str]:
    """The instructions after `FROM ... AS <stage>` up to the next FROM."""
    collected, inside = [], False
    for line in instructions():
        if line.startswith("FROM "):
            inside = bool(re.search(rf"\bAS\s+{stage}\s*$", line))
            continue
        if inside:
            collected.append(line)
    return collected


def pip_install_line() -> str:
    return next(line for line in stage_instructions("runtime") if "pip install" in line)


@pytest.mark.parametrize(
    "package",
    ["pytest", "iniconfig", "pluggy", "pygments", "uvloop", "httptools", "watchfiles", "websockets"],
)
def test_runtime_image_leaves_out_packages_the_extension_never_uses(package):
    assert re.search(rf"\b{package}\b", pip_install_line().split("pip install")[0])
    # The exclusion only means something while the package really is a requirement.
    assert re.search(rf"^{package}==", REQUIREMENTS, flags=re.IGNORECASE | re.MULTILINE)


def test_runtime_image_does_not_install_the_postgresql_driver():
    runtime = " ".join(stage_instructions("runtime"))
    assert "psycopg2" not in runtime
    assert "requirements-postgres" not in runtime


def test_the_extension_is_fixed_to_sqlite_so_the_postgresql_driver_is_not_needed():
    compose = (ROOT / "extension" / "compose.yaml").read_text(encoding="utf-8")
    assert "DATABASE_URL: sqlite:" in compose


def test_pip_downloads_and_the_go_caches_are_build_cache_mounts():
    assert "--mount=type=cache,target=/root/.cache/pip" in pip_install_line()
    helper = stage_instructions("helper")
    assert any("go mod download" in line and "--mount=type=cache,target=/go/pkg/mod" in line for line in helper)
    compile_line = next(line for line in helper if "go build" in line)
    assert "--mount=type=cache,target=/go/pkg/mod" in compile_line
    assert "--mount=type=cache,target=/root/.cache/go-build" in compile_line


def test_pip_cache_is_not_disabled_for_the_cache_mount_to_help():
    assert "PIP_NO_CACHE_DIR" not in DOCKERFILE


def test_render_waits_for_the_helper_so_the_two_never_compete_for_memory():
    render = stage_instructions("render")
    wait = next((i for i, line in enumerate(render) if "from=helper" in line), None)
    build = next(i for i, line in enumerate(render) if "extension.build_ui" in line)
    assert wait is not None and wait < build


def test_the_helper_stage_is_defined_before_the_stage_that_mounts_from_it():
    """BuildKit refuses `--mount=from=helper` above the stage's definition ("stage needs to be defined")."""
    stages = re.findall(r"^FROM\s+.*?\bAS\s+(\w+)\s*$", DOCKERFILE, flags=re.MULTILINE)
    assert stages.index("helper") < stages.index("render")


def test_go_compiler_parallelism_stays_capped():
    assert re.search(r"go build -p 2\b", DOCKERFILE)


@pytest.mark.parametrize(
    "pattern",
    ["extension/helper/**/*_test.go", "extension/ui-src/*.test.mjs", "sample_data", "deploy", "tests", ".venv"],
)
def test_the_build_context_leaves_out_tests_fixtures_and_the_virtualenv(pattern):
    assert pattern in DOCKERIGNORE


def test_every_ui_script_the_image_copies_is_not_ignored():
    copied = re.search(r"COPY (extension/ui-src/\S+(?: extension/ui-src/\S+)*) /ui/", DOCKERFILE)
    assert copied, "the final stage should copy the page scripts"
    for path in copied.group(1).split():
        assert ".test." not in path
        assert (ROOT / path).is_file(), path


def test_no_instruction_copies_the_helper_test_files_or_fixtures():
    for line in instructions():
        if line.startswith("COPY"):
            assert not re.search(r"_test\.go|\.test\.mjs|\bsample_data\b|\bdeploy\b", line), line


def test_the_backend_has_a_memory_limit_that_leaves_room_above_the_measured_peak():
    """Measured peak was 80 MiB; the limit protects a 4 GB Docker VM without starving a large cluster's JSON."""
    compose = (ROOT / "extension" / "compose.yaml").read_text(encoding="utf-8")
    limit = re.search(r"^\s+mem_limit:\s*(\d+)([mg])\s*$", compose, flags=re.MULTILINE)
    assert limit, "extension/compose.yaml should set mem_limit"
    mebibytes = int(limit.group(1)) * (1024 if limit.group(2) == "g" else 1)
    assert 256 <= mebibytes <= 1024
