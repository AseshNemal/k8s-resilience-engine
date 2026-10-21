"""
Live placement-drift experiments on a local KinD cluster (Study Guide section 10).

Usage (from the project root):
  .\\.venv\\Scripts\\python.exe experiments\\placement_drift\\run_live_experiments.py ^
      --context kind-resilience-research --repetitions 5

Only local `kind-` contexts are accepted. The user's current kubectl context is not used or changed.
Results: experiments/placement_drift/results/<timestamp>/ (one JSON per run + summary.json/.md).
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from experiments.placement_drift.live.kubectl import Kubectl  # noqa: E402
from experiments.placement_drift.live.runner import LiveExperimentRunner  # noqa: E402
from experiments.placement_drift.live.scenarios import SCENARIOS, SCENARIOS_BY_ID  # noqa: E402
from experiments.placement_drift.live.summary import summarise  # noqa: E402
from experiments.placement_drift.live.workloads import STANDIN  # noqa: E402

CONFIGS = STANDIN.configs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--context", required=True, help="kubeconfig context, e.g. kind-resilience-research")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--configs", nargs="+", default=list(CONFIGS), choices=list(CONFIGS))
    parser.add_argument("--scenarios", nargs="+", default=[s.scenario_id for s in SCENARIOS],
                        choices=list(SCENARIOS_BY_ID))
    parser.add_argument("--results-dir", type=Path, default=None)
    args = parser.parse_args()

    results_dir = args.results_dir or ROOT / "experiments" / "placement_drift" / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    scenarios = [SCENARIOS_BY_ID[s] for s in args.scenarios]
    total = args.repetitions * len(args.configs) * len(scenarios)
    done = 0

    def progress(record):
        nonlocal done
        done += 1
        if record is None:
            print(f"[{done}/{total}] FAILED (see results JSON)", flush=True)
            return
        d = "n/a" if record.d_rate is None else f"{record.d_rate:.2f}"
        print(f"[{done}/{total}] {record.scenario_id:<26} {record.config:<10} run {record.repetition}: "
              f"D_rate={d}  target {record.target_status_t0} -> {record.target_status_t1}"
              f"{'' if record.stable_after_disruption else '  (did not settle)'}", flush=True)

    runner = LiveExperimentRunner(Kubectl(args.context, STANDIN.namespace), STANDIN.configs,
                                  list(STANDIN.deployments), results_dir, workload=STANDIN)
    print(f"Running {total} live runs on {args.context}; results -> {results_dir}", flush=True)
    runner.run(scenarios, args.configs, args.repetitions, on_record=progress)
    summarise(results_dir)
    print((results_dir / "summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
