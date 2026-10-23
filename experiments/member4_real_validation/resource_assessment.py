"""Can this machine host a second, disposable kind cluster beside the Docker Desktop one? Read-only, and honest about guesses.

It reads what is measurable (host memory, the Docker VM's memory and CPUs, the memory every running container uses right
now, which kind clusters exist) with a short fixed list of read-only commands, and does arithmetic on it.

What drives a verdict. Only measured figures. The memory a kind node needs is taken from kind nodes ALREADY running on this
machine (the Docker Desktop cluster is itself kind), which is a proxy, not a measurement of the new cluster. From that:

* ``need_lower_bound``: one control-plane node plus the workers, at the largest figure measured for each kind. It leaves out
  the application and everything else, so the real need is higher. If this alone exceeds the VM's headroom the answer is
  INSUFFICIENT whatever else is assumed.
* if the nodes fit but leave less than the safety margin (512 MiB unless changed) the answer is INSUFFICIENT too: the
  application will need something, and a margin that thin cannot hold it;
* if they fit with the margin to spare, the answer is at best UNCERTAIN until the application's memory is supplied from a
  measurement (``app_memory_mib``); this module does not guess it. A verdict of SUFFICIENT needs that figure.
* with no kind node to measure, the answer is UNKNOWN.

The VM headroom is the VM's memory minus the sum of what the containers use; it ignores the VM's own overhead and page cache,
so it is an upper bound on what is free. Memory use on a developer machine moves (the existing cluster's control-plane read 1.10,
0.79 and 0.81 GiB within minutes of a restart), so several samples are taken and each container is counted at its LARGEST
reading: a quiet moment must not make the machine look roomier than it is.

Exit status when run as a program: 0 SUFFICIENT, 3 INSUFFICIENT, 4 UNCERTAIN or UNKNOWN.

    python -m experiments.member4_real_validation.resource_assessment [--profile minimal|recommended] [--app-memory-mib N]
"""

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

MIB, GIB = 1024**2, 1024**3

# The only commands this module will run. All of them only read.
READ_ONLY_COMMANDS = (
    ("uname", "-m"),
    ("sysctl", "-n", "hw.memsize"),
    ("docker", "info", "--format", "{{.MemTotal}}\t{{.NCPU}}"),
    ("docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"),
    ("kind", "get", "clusters"),
)

PROFILES = {"minimal": 2, "recommended": 3}      # worker nodes; one control-plane node in both
_UNITS = {"B": 1, "KiB": 1024, "MiB": MIB, "GiB": GIB, "kB": 1000, "MB": 1000**2, "GB": 1000**3}
_NODE = re.compile(r"-(control-plane|worker\d*)$")


class Runner:
    """Runs only the commands in READ_ONLY_COMMANDS; returns None when one is unavailable or fails."""

    def __call__(self, command: Sequence[str]) -> str | None:
        if tuple(command) not in READ_ONLY_COMMANDS:
            raise ValueError(f"{list(command)} is not one of the read-only commands this tool runs")
        try:
            done = subprocess.run(list(command), capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return done.stdout if done.returncode == 0 else None


@dataclass(frozen=True)
class Facts:
    architecture: str | None
    host_memory_bytes: int | None
    docker_vm_memory_bytes: int | None
    docker_vm_cpus: int | None
    container_memory_bytes: dict[str, int] = field(default_factory=dict)
    kind_clusters: tuple[str, ...] = ()


def parse_memory(text: str) -> int | None:
    match = re.match(r"^\s*([0-9.]+)\s*([A-Za-z]+)\s*$", text or "")
    if not match or match.group(2) not in _UNITS:
        return None
    return int(float(match.group(1)) * _UNITS[match.group(2)])


def collect(run: Callable[[Sequence[str]], str | None] = Runner()) -> Facts:
    def number(text: str | None) -> int | None:
        try:
            return int((text or "").strip())
        except ValueError:
            return None
    info = (run(("docker", "info", "--format", "{{.MemTotal}}\t{{.NCPU}}")) or "").strip().split("\t")
    containers = {}
    for line in (run(("docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}")) or "").splitlines():
        name, _, usage = line.partition("\t")
        used = parse_memory(usage.split("/")[0])
        if name and used is not None:
            containers[name.strip()] = used
    architecture = (run(("uname", "-m")) or "").strip() or None
    clusters = tuple(c for c in (run(("kind", "get", "clusters")) or "").split() if c)
    return Facts(architecture, number(run(("sysctl", "-n", "hw.memsize"))), number(info[0]) if info else None,
                 number(info[1]) if len(info) > 1 else None, containers, clusters)


def merge_samples(samples: Sequence[Facts]) -> Facts:
    """One ``Facts`` from several readings: the most each container was seen to use, and the first reading of the rest."""
    first = samples[0]
    names = {name for sample in samples for name in sample.container_memory_bytes}
    largest = {name: max(s.container_memory_bytes.get(name, 0) for s in samples) for name in names}
    return Facts(first.architecture, first.host_memory_bytes, first.docker_vm_memory_bytes, first.docker_vm_cpus, largest,
                 first.kind_clusters)


@dataclass(frozen=True)
class Assessment:
    verdict: str                      # SUFFICIENT | INSUFFICIENT | UNCERTAIN | UNKNOWN
    profile: str
    workers: int
    facts: dict[str, Any]
    headroom_bytes: int | None
    need_lower_bound_bytes: int | None
    proxies_bytes: dict[str, int]
    reasons: list[str]
    unmeasured: list[str]
    host_note: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess(facts: Facts, *, profile: str = "minimal", app_memory_mib: float | None = None, safety_margin_mib: float = 512.0,
           host_reserve_gib: float = 2.0) -> Assessment:
    workers = PROFILES[profile]
    reasons: list[str] = []
    unmeasured = ["the memory the application's pods need (supply app_memory_mib from a measurement)",
                  "the memory a NEW kind node needs (taken from existing kind nodes as a proxy)"]
    vm, used = facts.docker_vm_memory_bytes, sum(facts.container_memory_bytes.values())
    headroom = None if vm is None else vm - used

    control = [b for n, b in facts.container_memory_bytes.items() if (m := _NODE.search(n)) and m.group(1) == "control-plane"]
    worker = [b for n, b in facts.container_memory_bytes.items() if (m := _NODE.search(n)) and m.group(1).startswith("worker")]
    proxies = {k: max(v) for k, v in (("control-plane", control), ("worker", worker)) if v}
    lower = None
    if "control-plane" in proxies and "worker" in proxies:
        lower = proxies["control-plane"] + workers * proxies["worker"]

    host_note = None
    if facts.host_memory_bytes and vm:
        left = facts.host_memory_bytes - vm
        host_note = (f"host {facts.host_memory_bytes / GIB:.1f} GiB, Docker VM {vm / GIB:.2f} GiB, leaving about "
                     f"{left / GIB:.1f} GiB for macOS and everything else")
        if left < host_reserve_gib * GIB:
            reasons.append(f"less than {host_reserve_gib:g} GiB would be left for the host if the VM were enlarged")

    if headroom is None:
        verdict, reasons = "UNKNOWN", ["Docker's memory could not be read (is Docker running?)"] + reasons
    elif lower is None:
        verdict = "UNKNOWN"
        reasons.append("no kind node is running on this machine to take a per-node memory figure from; create the control-plane "
                       "node alone first and measure it")
    elif lower > headroom:
        verdict = "INSUFFICIENT"
        reasons.append(f"one control-plane node and {workers} workers need at least {lower / GIB:.2f} GiB at the sizes measured on "
                       f"this machine, and the Docker VM has about {headroom / GIB:.2f} GiB free: the shortfall exists before "
                       f"the application is counted")
    elif lower + safety_margin_mib * MIB > headroom:
        verdict = "INSUFFICIENT"
        reasons.append(f"the nodes alone (at least {lower / GIB:.2f} GiB) would leave only {(headroom - lower) / MIB:.0f} MiB of the "
                       f"{headroom / GIB:.2f} GiB the Docker VM has free, below the {safety_margin_mib:g} MiB margin, and the "
                       f"application has not been counted")
    elif app_memory_mib is None:
        verdict = "UNCERTAIN"
        reasons.append("the nodes alone would fit, but the application's memory has not been measured")
    else:
        need = lower + app_memory_mib * MIB + safety_margin_mib * MIB
        verdict = "SUFFICIENT" if need <= headroom else "INSUFFICIENT"
        unmeasured = unmeasured[1:]
        reasons.append(f"need about {need / GIB:.2f} GiB (nodes, the supplied application figure, {safety_margin_mib:g} MiB margin) "
                       f"against {headroom / GIB:.2f} GiB free")
    return Assessment(verdict, profile, workers, asdict(facts), headroom, lower, proxies, reasons, unmeasured, host_note)


def render(a: Assessment) -> str:
    f = a.facts

    def gib(value: int | None) -> str:
        return "unknown" if value is None else f"{value / GIB:.2f} GiB"
    lines = [
        f"Resource assessment for profile '{a.profile}' (1 control-plane + {a.workers} workers): {a.verdict}",
        f"  host: {f['architecture']}, {gib(f['host_memory_bytes'])}; Docker VM: {gib(f['docker_vm_memory_bytes'])}, "
        f"{f['docker_vm_cpus']} CPUs",
        f"  in use by {len(f['container_memory_bytes'])} running containers: {gib(sum(f['container_memory_bytes'].values()))}; "
        f"VM headroom: {gib(a.headroom_bytes)}",
        f"  kind clusters present: {', '.join(f['kind_clusters']) or 'none'}",
        "  per-node proxies measured on existing kind nodes: "
        + (", ".join(f"{k} {gib(v)}" for k, v in a.proxies_bytes.items()) or "none"),
        f"  lower bound for the nodes alone: {gib(a.need_lower_bound_bytes)}",
    ]
    lines += [f"  - {reason}" for reason in a.reasons]
    if a.host_note:
        lines.append(f"  host: {a.host_note}")
    lines += [f"  not measured: {item}" for item in a.unmeasured]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, *, run: Callable[[Sequence[str]], str | None] | None = None,
         sleep: Callable[[float], None] = time.sleep) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile", choices=sorted(PROFILES), default="minimal")
    parser.add_argument("--app-memory-mib", type=float, help="the application's measured memory, in MiB")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--samples", type=int, default=3, help="readings to take; each container counts at its largest")
    parser.add_argument("--interval-s", type=float, default=5.0)
    args = parser.parse_args(argv)
    reader = run or Runner()
    readings = []
    for index in range(max(1, args.samples)):
        if index:
            sleep(args.interval_s)
        readings.append(collect(reader))
    result = assess(merge_samples(readings), profile=args.profile, app_memory_mib=args.app_memory_mib)
    print(json.dumps(result.to_dict(), indent=2) if args.json else render(result))
    return {"SUFFICIENT": 0, "INSUFFICIENT": 3}.get(result.verdict, 4)


if __name__ == "__main__":      # pragma: no cover
    sys.exit(main())
