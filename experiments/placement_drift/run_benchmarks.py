"""
Interactive Chaos Engineering Benchmark Runner.
Component: Component 2 - Deployment Topology Modeling
Author: P.A.M. Minara (IT23154476)

Executes all 8 Section 10 disruption scenarios, outputs live ASCII transition tables
to the terminal, and exports publication-grade Markdown and JSON benchmark artifacts.
"""
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List

# Running this file directly puts only its own folder on sys.path; add the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from experiments.placement_drift.disruption_orchestrator import DisruptionOrchestrator  # noqa: E402
from experiments.placement_drift.disruption_reporter import DisruptionReporter  # noqa: E402
from experiments.placement_drift.drift_transition_matrix import DriftTransitionMatrix  # noqa: E402
from experiments.placement_drift.scenario_models import ScenarioResult  # noqa: E402
from experiments.placement_drift.snapshot_repository import SnapshotRepository  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def _print_scenario_banner(result: ScenarioResult) -> None:
    """Prints a formatted summary for an individual scenario result."""
    total_migrated = sum(t.pod_count for t in result.transitions)
    status_icon = "[PASS]" if result.rollback_successful else "[FAIL]"
    print(
        f"  {status_icon} Scenario: {result.scenario_id:<28} | "
        f"D_rate: {_pct(result.d_rate)} | "
        f"Delta_E: {_pct(result.delta_e_rate)} | "
        f"Migrated: {total_migrated:>2} pods | "
        f"Latency: {result.runtime_ms:.1f}ms"
    )


def _render_transition_matrix(
    repo: SnapshotRepository, scenario_id: str
) -> None:
    """Extracts T0 and T1 snapshots and renders the ASCII transition matrix."""
    t0 = repo.get(scenario_id, "T0")
    t1 = repo.get(scenario_id, "T1")
    if t0 and t1:
        nodes, matrix, _ = DriftTransitionMatrix.build_matrix(t0, t1)
        ascii_table = DriftTransitionMatrix.format_ascii_table(nodes, matrix)
        print(f"\n--- State Transition Matrix ({scenario_id}) ---")
        print(ascii_table)
        print("-" * 55 + "\n")


def _write_artifact_files(
    results: List[ScenarioResult],
    aggregate_metrics: Dict[str, float],
    output_dir: Path,
) -> tuple[Path, Path]:
    """Writes Markdown and JSON benchmark artifacts to the target directory."""
    md_content = DisruptionReporter.generate_markdown_report(results, aggregate_metrics)
    json_data = DisruptionReporter.generate_json_payload(results, aggregate_metrics)

    md_file = output_dir / "benchmark_report.md"
    json_file = output_dir / "benchmark_report.json"

    with open(md_file, "w", encoding="utf-8") as f:
        f.write(md_content)

    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2)

    return md_file, json_file


def run_benchmarks(output_dir: Path | None = None) -> Dict[str, float]:
    """
    Executes all 8 Section 10 disruption scenarios, outputs live matrices,
    and saves the Markdown and JSON reports to output_dir (default: this folder).
    """
    print("=" * 80)
    print("COMPONENT 2: PLACEMENT DRIFT SCENARIO RUNNER (DRY-RUN SIMULATION)")
    print("Author: P.A.M. Minara (IT23154476) | Research Component 2")
    print("=" * 80 + "\n")

    repo = SnapshotRepository()
    orchestrator = DisruptionOrchestrator(repository=repo)
    results = orchestrator.run_all()
    metrics = orchestrator.compute_aggregate_metrics(results)

    print("\n" + "=" * 80)
    print("EXECUTION RESULTS SUMMARY")
    print("=" * 80)
    for r in results:
        _print_scenario_banner(r)
        if r.transitions:
            _render_transition_matrix(repo, r.scenario_id)

    output_dir = output_dir or Path(__file__).parent
    md_path, json_path = _write_artifact_files(results, metrics, output_dir)

    print("=" * 80)
    print("AGGREGATE BENCHMARK METRICS")
    print("=" * 80)
    print(f"Total Scenarios Evaluated: {int(metrics['total_scenarios'])}")
    print(f"Scenarios Completed:      {int(metrics['completed_count'])}")
    print(f"Execution Mode:            {metrics['execution_mode']} (scripted placements)")
    print(f"Mean Placement Drift Rate: {_pct(metrics['mean_d_rate'])}")
    print(f"Max Placement Drift Rate:  {_pct(metrics['max_d_rate'])}")
    print(f"Total Execution Latency:   {metrics['total_runtime_ms']:.2f} ms")
    print("-" * 80)
    print(f"Saved Markdown report:     {md_path}")
    print(f"Saved Machine JSON Data:   {json_path}")
    print("=" * 80)

    return metrics


if __name__ == "__main__":
    run_benchmarks()
