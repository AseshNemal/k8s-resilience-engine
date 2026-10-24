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
import subprocess
import sys
import time
from collections.abc import Callable, Sequence

from app.engine.recommendation_evaluation_validation.validation_loop.resources import (  # noqa: F401  (re-exported: the arithmetic lives there)
    GIB,
    MIB,
    PROFILES,
    Assessment,
    Facts,
    assess,
    merge_samples,
    parse_memory,
)

# The only commands this module will run. All of them only read.
READ_ONLY_COMMANDS = (
    ("uname", "-m"),
    ("sysctl", "-n", "hw.memsize"),
    ("docker", "info", "--format", "{{.MemTotal}}\t{{.NCPU}}"),
    ("docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"),
    ("kind", "get", "clusters"),
)

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
