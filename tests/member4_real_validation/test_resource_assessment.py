"""The resource assessment: read-only, driven by measured figures, and unable to say SUFFICIENT on a guess."""

import pytest

from experiments.member4_real_validation import resource_assessment as ra
from experiments.member4_real_validation.resource_assessment import GIB, MIB, Facts, assess, collect, merge_samples, parse_memory

# Figures read from the developer's machine on 2026-10-23 with read-only docker commands (a fixture, not a result about anything else).
OBSERVED = Facts(
    architecture="arm64", host_memory_bytes=8589934592, docker_vm_memory_bytes=4107005952, docker_vm_cpus=5,
    container_memory_bytes={
        "desktop-control-plane": int(1.096 * GIB), "desktop-worker": int(467.7 * MIB), "desktop-worker2": int(710.6 * MIB),
        "resilience-trace-jaeger": int(59.75 * MIB), "kind-cloud-provider": int(85.86 * MIB), "kind-registry-mirror": int(29.5 * MIB)},
    kind_clusters=("desktop",))

# A second reading minutes later: the same machine, noticeably less memory in use.
LATER = Facts(
    architecture="arm64", host_memory_bytes=8589934592, docker_vm_memory_bytes=4107005952, docker_vm_cpus=5,
    container_memory_bytes={
        "desktop-control-plane": int(813.4 * MIB), "desktop-worker": int(376.6 * MIB), "desktop-worker2": int(561.3 * MIB),
        "resilience-trace-jaeger": int(17.5 * MIB), "kind-cloud-provider": int(18.2 * MIB), "kind-registry-mirror": int(8.4 * MIB)},
    kind_clusters=("desktop",))

ROOMY = Facts("arm64", 16 * GIB, 12 * GIB, 8, {"x-control-plane": GIB, "x-worker": 500 * MIB}, ("x",))


@pytest.mark.parametrize("text, value", [("1.096GiB", int(1.096 * GIB)), ("467.7MiB", int(467.7 * MIB)), ("59.75MiB", int(59.75 * MIB)),
                                         ("29.5MiB ", int(29.5 * MIB)), ("12kB", 12000)])
def test_docker_stats_memory_is_parsed(text, value):
    assert parse_memory(text) == value


@pytest.mark.parametrize("text", ["", "n/a", "12 parsecs", None])
def test_an_unreadable_figure_is_none(text):
    assert parse_memory(text) is None


def test_on_the_machine_as_observed_even_the_nodes_alone_do_not_fit_the_minimal_profile():
    result = assess(OBSERVED, profile="minimal")

    assert result.verdict == "INSUFFICIENT"
    assert result.headroom_bytes == pytest.approx(4107005952 - sum(OBSERVED.container_memory_bytes.values()))
    assert result.proxies_bytes["worker"] == int(710.6 * MIB)          # the largest worker measured, not an average
    assert result.need_lower_bound_bytes == result.proxies_bytes["control-plane"] + 2 * result.proxies_bytes["worker"]
    assert result.need_lower_bound_bytes > result.headroom_bytes
    assert any("before the application is counted" in reason for reason in result.reasons)


def test_the_recommended_profile_needs_more_than_the_minimal_one():
    assert assess(OBSERVED, profile="recommended").need_lower_bound_bytes > assess(OBSERVED, profile="minimal").need_lower_bound_bytes


def test_supplying_an_application_figure_cannot_rescue_a_shortfall_in_the_nodes_alone():
    assert assess(OBSERVED, app_memory_mib=1).verdict == "INSUFFICIENT"


def test_when_the_nodes_fit_the_answer_is_uncertain_until_the_application_is_measured():
    result = assess(ROOMY)

    assert result.verdict == "UNCERTAIN" and any("not been measured" in r for r in result.reasons)
    assert any("application" in item for item in result.unmeasured)


def test_sufficient_needs_a_supplied_application_figure_and_a_margin():
    assert assess(ROOMY, app_memory_mib=1024).verdict == "SUFFICIENT"
    assert assess(ROOMY, app_memory_mib=12 * 1024).verdict == "INSUFFICIENT"
    assert assess(ROOMY, app_memory_mib=1024).unmeasured == ["the memory a NEW kind node needs (taken from existing kind nodes as a proxy)"]


def test_with_no_kind_node_to_measure_the_answer_is_unknown_not_a_guess():
    result = assess(Facts("arm64", 8 * GIB, 6 * GIB, 4, {"web": GIB}, ()))

    assert result.verdict == "UNKNOWN" and result.need_lower_bound_bytes is None


def test_docker_not_running_is_unknown():
    assert assess(Facts("arm64", 8 * GIB, None, None)).verdict == "UNKNOWN"


def test_enlarging_the_vm_beyond_what_the_host_can_spare_is_flagged():
    result = assess(Facts("arm64", 8 * GIB, 7 * GIB, 4, ROOMY.container_memory_bytes, ("x",)))

    assert any("left for the host" in reason for reason in result.reasons) and "macOS" in result.host_note


def test_the_collector_asks_only_read_only_questions_and_survives_missing_tools():
    asked = []

    def run(command):
        asked.append(tuple(command))
        return {"uname": "arm64\n", "sysctl": "8589934592\n"}.get(command[0])

    facts = collect(run)

    assert set(asked) <= set(ra.READ_ONLY_COMMANDS) and facts.architecture == "arm64"
    assert facts.docker_vm_memory_bytes is None and facts.container_memory_bytes == {} and facts.kind_clusters == ()


def test_the_runner_refuses_any_command_that_is_not_on_the_read_only_list():
    for command in (("docker", "run", "x"), ("kind", "create", "cluster"), ("kubectl", "get", "nodes"), ("docker", "kill", "x")):
        with pytest.raises(ValueError):
            ra.Runner()(command)


def test_the_program_exits_3_when_the_machine_cannot_host_the_cluster(capsys):
    outputs = {
        ("uname", "-m"): "arm64\n", ("sysctl", "-n", "hw.memsize"): "8589934592\n",
        ("docker", "info", "--format", "{{.MemTotal}}\t{{.NCPU}}"): "4107005952\t5\n",
        ("docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"):
            "desktop-control-plane\t1.096GiB / 3.825GiB\ndesktop-worker\t467.7MiB / 3.825GiB\ndesktop-worker2\t710.6MiB / 3.825GiB\n",
        ("kind", "get", "clusters"): "desktop\n"}

    code = ra.main([], run=lambda command: outputs.get(tuple(command)), sleep=lambda seconds: None)

    assert code == 3 and "INSUFFICIENT" in capsys.readouterr().out


def test_nodes_that_fit_with_almost_nothing_to_spare_are_insufficient_not_uncertain():
    result = assess(LATER, profile="minimal")

    assert result.need_lower_bound_bytes < result.headroom_bytes           # they do fit ...
    assert result.verdict == "INSUFFICIENT"                                 # ... but the application cannot be added to a 185 MiB margin
    assert any("below the 512 MiB margin" in reason for reason in result.reasons)


def test_a_looser_margin_is_a_choice_that_can_be_made_but_is_not_the_default():
    assert assess(LATER, profile="minimal", safety_margin_mib=100.0).verdict == "UNCERTAIN"


def test_samples_are_merged_at_each_containers_largest_reading():
    merged = merge_samples([LATER, OBSERVED])

    assert merged.container_memory_bytes["desktop-control-plane"] == int(1.096 * GIB)
    assert merged.container_memory_bytes["desktop-worker"] == int(467.7 * MIB)
    assert assess(merged, profile="minimal").verdict == "INSUFFICIENT"


def test_the_program_takes_several_readings_and_sleeps_between_them(capsys):
    outputs = {("uname", "-m"): "arm64\n", ("sysctl", "-n", "hw.memsize"): "8589934592\n",
               ("docker", "info", "--format", "{{.MemTotal}}\t{{.NCPU}}"): "4107005952\t5\n",
               ("docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"): "desktop-control-plane\t800MiB / 3.8GiB\n",
               ("kind", "get", "clusters"): "desktop\n"}
    slept = []

    ra.main(["--samples", "3", "--interval-s", "2"], run=lambda c: outputs.get(tuple(c)), sleep=slept.append)

    assert slept == [2.0, 2.0]
