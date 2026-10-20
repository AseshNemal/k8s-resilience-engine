"""
Disruption Reporter: Formats scenario outcomes into Markdown and JSON.
Component: Component 2 - Deployment Topology Modeling
"""
from typing import Any, Dict, List

from experiments.placement_drift.scenario_models import ScenarioResult

DRY_RUN_NOTICE = (
    "> **DRY-RUN SIMULATION.** T0 and T1 placements are scripted in the scenario code. "
    "These numbers illustrate the metrics and must not be reported as observed scheduler behaviour."
)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


class DisruptionReporter:

    @classmethod
    def _observations(cls, results: List[ScenarioResult]) -> List[str]:
        measured = [r for r in results if r.d_rate is not None]
        if not measured:
            return ["- No scenario produced a measurable D_rate."]
        top = max(r.d_rate for r in measured)
        top_ids = ", ".join(f"`{r.scenario_id}`" for r in measured if r.d_rate == top)
        newly_exposed = [r.scenario_id for r in measured if (r.delta_e_rate or 0) > 0]
        lines = [f"- Highest D_rate ({_pct(top)}): {top_ids}."]
        lines.append(
            f"- Exposure rate increased in: {', '.join(f'`{s}`' for s in newly_exposed)}."
            if newly_exposed else "- No scenario increased the exposure rate."
        )
        failed_rollbacks = [r.scenario_id for r in results if not r.rollback_successful]
        lines.append(
            f"- Rollback failed in: {', '.join(failed_rollbacks)}."
            if failed_rollbacks else "- Every scenario reported a successful rollback."
        )
        return lines

    @classmethod
    def generate_markdown_report(
        cls,
        results: List[ScenarioResult],
        aggregate_metrics: Dict[str, Any],
    ) -> str:
        lines = ["# Component 2: Placement Drift Scenario Report", ""]
        if not aggregate_metrics.get("is_experimental_evidence"):
            lines += [DRY_RUN_NOTICE, ""]
        lines += [
            "## 1. Aggregate Summary",
            f"- **Execution Mode:** {aggregate_metrics.get('execution_mode', 'unknown')}",
            f"- **Total Scenarios:** {int(aggregate_metrics.get('total_scenarios', 0))}",
            f"- **Completed:** {int(aggregate_metrics.get('completed_count', 0))}",
            f"- **Mean Placement Drift Rate ($D_{{rate}}$):** {_pct(aggregate_metrics.get('mean_d_rate'))}",
            f"- **Max Placement Drift Rate:** {_pct(aggregate_metrics.get('max_d_rate'))}",
            f"- **Total Runtime:** {aggregate_metrics.get('total_runtime_ms', 0.0):.2f} ms",
            "",
            "## 2. Scenario Results",
            "| Scenario ID | Disruption Type | Status | $D_{rate}$ | $\\Delta E_{rate}$ | Moved Pods | Latency (ms) | Rollback |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
        for r in results:
            moved = sum(t.pod_count for t in r.transitions)
            lines.append(
                f"| `{r.scenario_id}` | `{r.disruption_type.value}` | `{r.status.value}` | "
                f"{_pct(r.d_rate)} | {_pct(r.delta_e_rate)} | {moved} | {r.runtime_ms:.1f} | "
                f"{'Pass' if r.rollback_successful else 'Fail'} |"
            )
        lines += ["", "## 3. Observations (computed from the table above)", *cls._observations(results)]
        return "\n".join(lines)

    @classmethod
    def generate_json_payload(
        cls,
        results: List[ScenarioResult],
        aggregate_metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "aggregate_metrics": aggregate_metrics,
            "scenario_results": [r.model_dump() for r in results],
        }
