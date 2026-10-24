"""Can this machine host a second, disposable kind cluster beside the Docker Desktop one? The arithmetic, honest about guesses.

Moved here from ``experiments/member4_real_validation/resource_assessment.py`` (which now imports it) so that the extension's backend can
run the same assessment at runtime: code under ``experiments/`` is never imported by the product. This module reads nothing; it does arithmetic
on ``Facts`` that a caller measured (the command-line tool measures them with read-only commands; the backend reads them through the restricted
Docker proxy, see ``app/integrations/docker/engine.py``).

What drives a verdict. Only measured figures. The memory a kind node needs is taken from kind nodes ALREADY running on this machine (the Docker
Desktop cluster is itself kind), which is a proxy, not a measurement of the new cluster.

* ``need_lower_bound``: one control-plane node plus the workers, at the largest figure measured for each kind. It leaves out the application and
  everything else, so the real need is higher. If this alone exceeds the VM's headroom the answer is INSUFFICIENT whatever else is assumed;
* if the nodes fit but leave less than the safety margin (512 MiB unless changed) the answer is INSUFFICIENT too;
* if they fit with the margin to spare, the answer is at best UNCERTAIN until the application's memory is supplied from a measurement
  (``app_memory_mib``); this module does not guess it. A verdict of SUFFICIENT needs that figure;
* with no kind node to measure, the answer is UNKNOWN.

Only SUFFICIENT allows a disposable cluster to be created. INSUFFICIENT, UNKNOWN and UNCERTAIN never do: the page says what to change instead.
"""

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

MIB, GIB = 1024**2, 1024**3

PROFILES = {"minimal": 2, "recommended": 3}      # worker nodes; one control-plane node in both
_UNITS = {"B": 1, "KiB": 1024, "MiB": MIB, "GiB": GIB, "kB": 1000, "MB": 1000**2, "GB": 1000**3}
_NODE = re.compile(r"-(control-plane|worker\d*)$")




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


def facts_from_engine(info: dict[str, Any], container_memory_bytes: dict[str, int]) -> Facts:
    """``Facts`` from what the restricted Docker proxy reports (the VM's memory and CPUs, each container's memory in use).

    The host's own memory and architecture are not visible from inside the extension, so they stay unknown, and the host note is omitted rather
    than guessed. The kind clusters present are taken from the node containers running (``<cluster>-control-plane``).
    """
    clusters = tuple(sorted({m.group(1) for name in container_memory_bytes if (m := re.match(r"^(.+)-control-plane$", name))}))
    memory, cpus = info.get("memTotalBytes"), info.get("ncpu")
    return Facts(None, None, int(memory) if memory else None, int(cpus) if cpus else None, dict(container_memory_bytes), clusters)


def advice(assessment: "Assessment", *, safety_margin_mib: float = 512.0) -> list[str]:
    """What to change when a disposable cluster cannot be created, in plain terms. Empty when it can be. Computed from the figures only."""
    if assessment.verdict == "SUFFICIENT":
        return []
    out: list[str] = []
    vm = assessment.facts.get("docker_vm_memory_bytes")
    lower = assessment.need_lower_bound_bytes
    if assessment.verdict == "UNKNOWN":
        return ["Docker's memory or the size of a kind node could not be read, so nothing is created. Check that Docker Desktop is running "
                "and that the Docker Desktop cluster's nodes are up."]
    if lower is not None and vm is not None:
        needed_vm = lower + safety_margin_mib * MIB + sum(assessment.facts.get("container_memory_bytes", {}).values())
        out.append(f"Raise Docker Desktop's memory to at least {needed_vm / GIB:.1f} GiB (Settings → Resources); it is {vm / GIB:.1f} GiB now. "
                   "That covers the test nodes and a safety margin, not yet the application.")
    out.append("Or stop what is not needed while testing, for example turn Kubernetes off in Docker Desktop's own cluster if you do not need it.")
    if assessment.verdict == "UNCERTAIN":
        out.append("The nodes would fit, but the application's memory has not been measured, so a cluster is not created on a guess. Give a "
                   "measured figure for it and ask again.")
    return out

