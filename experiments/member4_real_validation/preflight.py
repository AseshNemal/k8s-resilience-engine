"""Is everything in place for the first real validation? A read-only checklist; it creates and changes nothing.

It runs the resource assessment, loads the designation, checks that the credentials file is the cluster's own, that the kind
cluster exists, that no earlier recovery is pending, and, only with ``--check-cluster`` and only when everything before it
passed, reads the designated cluster (identity, baseline graph, capacity) with the read-only gateway. It then lists the steps
that only a person can do. Each line is PASS, FAIL, WARN or PENDING (needs a person); the program exits 0 only when nothing is
FAIL and nothing is WARN, and PENDING lines are always printed because software cannot satisfy them.

    python -m experiments.member4_real_validation.preflight --designation FILE --kubeconfig FILE [--check-cluster]
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.engine.deployment_topology_modeling.discovery import discover
from app.engine.recommendation_evaluation_validation.validation import DisposableClusterDesignation, verify_cluster
from app.integrations.kind.recovery import pending_records
from app.integrations.kubernetes.validation_gateway import AmbientCredentialsError, KubernetesTestClusterGateway
from experiments.member4_real_validation import resource_assessment as ra

PASS, FAIL, WARN, PENDING = "PASS", "FAIL", "WARN", "PENDING"

PERSON_STEPS = (
    "A recommendation for the experiment application was computed against THIS cluster and approved through the approval gate by a named person",
    "A separate STOP_KIND_WORKER_CONTAINER authorization exists for each kill (two with a measured baseline), by a named person",
    "APPLY_TO_TEST_CLUSTER, FAIL_BASELINE_IN_TEST_CLUSTER and FAIL_NODE_IN_TEST_CLUSTER authorizations exist, by a named person",
    "A WriteApproval for this cluster's kube-system UID exists, in the person's own words",
    "The repetition count, the agreement thresholds and the probe pass rule were written down BEFORE the first run",
    "The person starting the run has read the abort and recovery procedure and knows where the recovery directory is",
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def load_designation(path: str) -> tuple[DisposableClusterDesignation, dict[str, Any]]:
    raw = json.loads(Path(path).read_text())
    required = ("name", "apiServer", "clusterUid", "namespace", "designatedBy", "kindCluster", "workers")
    missing = [key for key in required if not raw.get(key)]
    placeholders = [key for key in required if isinstance(raw.get(key), str) and raw[key].startswith("REPLACE")]
    if missing or placeholders:
        raise ValueError(f"the designation is incomplete: missing {missing}, still placeholders {placeholders}")
    designation = DisposableClusterDesignation(
        name=raw["name"], api_server=raw["apiServer"], cluster_uid=raw["clusterUid"], namespace=raw["namespace"],
        designated_by=raw["designatedBy"], context=raw.get("context"),
        node_names=tuple(raw["nodeNames"]) if raw.get("nodeNames") else None)
    return designation, raw


def run_preflight(designation_path: str | None, kubeconfig_path: str | None, *, recovery_dir: str | None = None,
                  check_cluster: bool = False, profile: str = "minimal", app_memory_mib: float | None = None,
                  facts: Callable[[], ra.Facts] | None = None,
                  gateway_factory: Callable[[str], Any] | None = None) -> list[Check]:
    checks: list[Check] = []
    assessment = ra.assess(ra.merge_samples([(facts or ra.collect)()]), profile=profile, app_memory_mib=app_memory_mib)
    checks.append(Check("resources", PASS if assessment.verdict == "SUFFICIENT" else FAIL if assessment.verdict == "INSUFFICIENT" else WARN,
                        f"{assessment.verdict}: " + "; ".join(assessment.reasons)))

    designation = raw = None
    if not designation_path or not Path(designation_path).is_file():
        checks.append(Check("designation", FAIL, "no designation file: the validation cluster has not been designated (it does not exist yet)"))
    else:
        try:
            designation, raw = load_designation(designation_path)
            checks.append(Check("designation", PASS, f"{designation.name}, namespace {designation.namespace}, designated by {designation.designated_by}"))
        except (ValueError, OSError, KeyError) as error:
            checks.append(Check("designation", FAIL, str(error)))

    if designation is not None:
        kind_name = raw["kindCluster"]
        present = tuple((facts or ra.collect)().kind_clusters)
        if kind_name.lower() in ("desktop", "docker-desktop"):
            checks.append(Check("kind cluster", FAIL, f"{kind_name!r} is the Docker Desktop cluster, never a validation cluster"))
        elif kind_name in present:
            checks.append(Check("kind cluster", PASS, f"{kind_name} exists"))
        else:
            checks.append(Check("kind cluster", FAIL, f"kind cluster {kind_name!r} does not exist (present: {', '.join(present) or 'none'})"))
        if recovery_dir:
            pending = pending_records(Path(recovery_dir))
            checks.append(Check("recovery", FAIL if pending else PASS,
                                f"{len(pending)} recovery record(s) not restored" if pending else "no pending recovery records"))

    if kubeconfig_path:
        try:
            KubernetesTestClusterGateway(kubeconfig_path, topology_reader=discover)
            checks.append(Check("credentials", PASS, "a credentials file of its own, not the default kubeconfig"))
        except AmbientCredentialsError as error:
            checks.append(Check("credentials", FAIL, str(error)))
    else:
        checks.append(Check("credentials", FAIL, "no credentials file named for the validation cluster"))

    if check_cluster:
        if any(c.status == FAIL for c in checks if c.name in ("designation", "kind cluster", "credentials")):
            checks.append(Check("cluster read", FAIL, "not attempted: the checks above must pass first"))
        else:
            try:
                gateway = (gateway_factory or (lambda path: KubernetesTestClusterGateway(path, topology_reader=discover)))(kubeconfig_path)
                verify_cluster(gateway.identity(), designation)
                graph = gateway.read_graph(designation.namespace)
                capacity = gateway.read_capacity()
                unready = [n.name for n in graph.nodes if n.ready is False]
                checks.append(Check("cluster read", FAIL if unready else PASS,
                                    f"identity matches the designation; {len(graph.nodes)} workers, {len(graph.services)} services in "
                                    f"{designation.namespace}; free CPU {sum(c.free_cpu or 0 for c in capacity if not c.control_plane):.1f} cores "
                                    f"on workers" + (f"; NOT READY: {unready}" if unready else "")))
            except Exception as error:      # noqa: BLE001  (a refusal or a read failure is the answer here)
                checks.append(Check("cluster read", FAIL, f"{type(error).__name__}: {error}"))
    else:
        checks.append(Check("cluster read", PENDING, "not run: pass --check-cluster once the cluster exists (read-only)"))

    checks += [Check("person", PENDING, step) for step in PERSON_STEPS]
    return checks


def render(checks: Sequence[Check]) -> str:
    lines = [f"{c.status:7} {c.name}: {c.detail}" for c in checks]
    blocked = [c for c in checks if c.status in (FAIL, WARN)]
    lines.append("")
    lines.append("NOT READY: " + ", ".join(sorted({c.name for c in blocked})) if blocked
                 else "Nothing automated blocks the run. The PENDING lines still need a person.")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, **kwargs: Any) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--designation")
    parser.add_argument("--kubeconfig")
    parser.add_argument("--recovery-dir")
    parser.add_argument("--check-cluster", action="store_true")
    parser.add_argument("--profile", choices=sorted(ra.PROFILES), default="minimal")
    parser.add_argument("--app-memory-mib", type=float)
    args = parser.parse_args(argv)
    checks = run_preflight(args.designation, args.kubeconfig, recovery_dir=args.recovery_dir, check_cluster=args.check_cluster,
                           profile=args.profile, app_memory_mib=args.app_memory_mib, **kwargs)
    print(render(checks))
    return 1 if any(c.status in (FAIL, WARN) for c in checks) else 0


if __name__ == "__main__":      # pragma: no cover
    sys.exit(main())
