"""
Records kubectl ground truth around real worker-node failures (model-accuracy evaluation).

Usage (from the project root):
  .\\.venv\\Scripts\\python.exe experiments\\placement_drift\\record_ground_truth.py --context kind-resilience-research

For each worker node in turn: record the namespace with kubectl, stop the node's KinD container
(docker stop), record which services still have a ready endpoint once Kubernetes marks the node
NotReady, then start the node again and wait until everything is ready. Before that, the Bookinfo
workload in bookinfo-exp is set up so both outcomes occur (details and ratings at 1 replica, reviews
with all replicas on one node); it is put back to 3 spread replicas at the end, also on error.

Stopping a node makes every pod on it unavailable for a few minutes, in every namespace of that
cluster. Only local `kind-` contexts are accepted.
Results: experiments/single-node-failure/bookinfo-exp-<timestamp>/<node>/ (read by the accuracy panel).
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from experiments.placement_drift.live.ground_truth_recorder import GroundTruthRecorder, MixedSetup  # noqa: E402
from experiments.placement_drift.live.kubectl import Kubectl  # noqa: E402
from experiments.placement_drift.live.workloads import BOOKINFO  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--context", required=True, help="kubeconfig context, e.g. kind-resilience-research")
    parser.add_argument("--nodes", nargs="+", default=None, help="worker nodes to fail (default: every worker)")
    parser.add_argument("--no-mixed-setup", action="store_true", help="record the workload as it is")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    kube = Kubectl(args.context, BOOKINFO.namespace)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.out or ROOT / "experiments" / "single-node-failure" / f"bookinfo-exp-{stamp}"
    recorder = GroundTruthRecorder(kube, list(BOOKINFO.deployments), out)
    nodes = args.nodes or kube.worker_nodes()
    # The no-rule config first: a spread rule would keep the concentrated reviews pods Pending.
    setup = None if args.no_mixed_setup else MixedSetup(manifest=BOOKINFO.configs["A-no-rule"])

    print(f"Recording ground truth in {BOOKINFO.namespace} on {args.context}; failing {', '.join(nodes)}; results -> {out}", flush=True)
    try:
        if setup:
            print("Setting up: details and ratings at 1 replica, reviews on one node…", flush=True)
            recorder.apply_mixed_setup(setup)
        for i, node in enumerate(nodes, 1):
            case = out / node
            print(f"[{i}/{len(nodes)}] recording before state, then stopping {node}…", flush=True)
            recorder.record_before(case)
            outcome = recorder.fail_node(case, node)
            summary = ", ".join(f"{s} {'up' if ok else 'DOWN'}" for s, ok in sorted(outcome.items()))
            print(f"[{i}/{len(nodes)}] {node} failed: {summary}. Node started again.", flush=True)
    finally:
        if setup:
            print("Restoring 3 spread replicas…", flush=True)
            recorder.restore(setup)
    print(f"Done. Recordings in {out}", flush=True)


if __name__ == "__main__":
    main()
