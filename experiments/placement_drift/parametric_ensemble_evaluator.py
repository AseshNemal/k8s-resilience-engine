"""
Ensemble statistics over executed placement-drift scenarios.
Component: Component 2 - Deployment Topology Modeling

Every trial is a scenario that was actually run through BaseScenario.run(); no trial value is
typed in by hand. With the current dry-run scenarios the statistics describe the scripted
fixtures, so `is_experimental_evidence` is False and they must not be reported as findings.
"""
import math
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from experiments.placement_drift.constants import (
    DEFAULT_CHAOS_NAMESPACE,
    ScenarioExecutionMode,
    ScenarioStatus,
)
from experiments.placement_drift.disruption_orchestrator import DisruptionOrchestrator
from experiments.placement_drift.snapshot_repository import SnapshotRepository

# Two-sided 95% Student-t critical values t(0.975, df) for df = 1..30 (standard tables).
_T_975 = [
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
]
_Z_975 = 1.960  # normal approximation used for df > 30

DRY_RUN_NOTE = (
    "Dry-run simulation: T0 and T1 placements are scripted in the scenario code. These statistics "
    "describe the fixtures and are not observations of the Kubernetes scheduler."
)


def t_critical_95(df: int) -> float:
    return _T_975[df - 1] if 1 <= df <= len(_T_975) else _Z_975


class ParametricTrialResult(BaseModel):
    trial_id: str
    scenario_category: str
    target_service: str
    parameter_spec: str
    execution_mode: ScenarioExecutionMode
    d_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    delta_e_rate: Optional[float] = None
    migrated_pods: int = 0
    status: ScenarioStatus
    runtime_ms: float = 0.0


class EnsembleBenchmarkMetrics(BaseModel):
    total_trials: int
    successful_trials: int
    measured_trials: int = Field(description="Completed trials with a measurable D_rate")
    execution_mode: ScenarioExecutionMode
    is_experimental_evidence: bool
    evidence_note: str
    mean_d_rate: Optional[float] = None
    std_dev_d_rate: Optional[float] = Field(default=None, description="Sample standard deviation (n-1)")
    min_d_rate: Optional[float] = None
    max_d_rate: Optional[float] = None
    mean_delta_e_rate: Optional[float] = None
    confidence_interval_95: Optional[List[float]] = Field(
        default=None, description="mean +/- t(0.975, n-1) * s / sqrt(n), clipped to [0, 1]; None if n < 2"
    )
    ci_method: str = "Student-t, two-sided 95%"
    per_service_drift: Dict[str, float] = Field(default_factory=dict)
    per_disruption_drift: Dict[str, float] = Field(default_factory=dict)
    trials: List[ParametricTrialResult] = Field(default_factory=list)


def _mean(values: List[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


class ParametricEnsembleEvaluator:

    def __init__(
        self,
        repository: Optional[SnapshotRepository] = None,
        execution_mode: ScenarioExecutionMode = ScenarioExecutionMode.DRY_RUN_SIMULATION,
        namespace: str = DEFAULT_CHAOS_NAMESPACE,
    ):
        self.repository = repository or SnapshotRepository()
        self.execution_mode = execution_mode
        self.namespace = namespace

    def _run_trials(self) -> List[ParametricTrialResult]:
        trials = []
        for scenario_cls in DisruptionOrchestrator.ALL_SCENARIO_CLASSES:
            scenario = scenario_cls(
                namespace=self.namespace, execution_mode=self.execution_mode, repository=self.repository
            )
            result = scenario.run()
            trials.append(ParametricTrialResult(
                trial_id=result.scenario_id,
                scenario_category=result.disruption_type.value,
                target_service=scenario.target_service,
                parameter_spec=result.scenario_name,
                execution_mode=result.execution_mode,
                d_rate=result.d_rate,
                delta_e_rate=result.delta_e_rate,
                migrated_pods=sum(t.pod_count for t in result.transitions),
                status=result.status,
                runtime_ms=result.runtime_ms,
            ))
        return trials

    def run_ensemble(self) -> EnsembleBenchmarkMetrics:
        trials = self._run_trials()
        completed = [t for t in trials if t.status == ScenarioStatus.COMPLETED]
        measured = [t for t in completed if t.d_rate is not None]
        d_rates = [t.d_rate for t in measured]
        n = len(d_rates)

        mean_d = _mean(d_rates)
        std_dev = ci = None
        if n >= 2:
            std_dev = math.sqrt(sum((x - mean_d) ** 2 for x in d_rates) / (n - 1))
            half_width = t_critical_95(n - 1) * std_dev / math.sqrt(n)
            ci = [round(max(0.0, mean_d - half_width), 4), round(min(1.0, mean_d + half_width), 4)]

        def grouped(key: str) -> Dict[str, float]:
            groups: Dict[str, List[float]] = {}
            for t in measured:
                groups.setdefault(getattr(t, key), []).append(t.d_rate)
            return {k: round(_mean(v), 4) for k, v in sorted(groups.items())}

        deltas = [t.delta_e_rate for t in measured if t.delta_e_rate is not None]
        is_live = self.execution_mode == ScenarioExecutionMode.LIVE_CLUSTER
        r4 = lambda v: round(v, 4) if v is not None else None  # noqa: E731

        return EnsembleBenchmarkMetrics(
            total_trials=len(trials),
            successful_trials=len(completed),
            measured_trials=n,
            execution_mode=self.execution_mode,
            is_experimental_evidence=is_live,
            evidence_note="Live-cluster observations." if is_live else DRY_RUN_NOTE,
            mean_d_rate=r4(mean_d),
            std_dev_d_rate=r4(std_dev),
            min_d_rate=r4(min(d_rates)) if d_rates else None,
            max_d_rate=r4(max(d_rates)) if d_rates else None,
            mean_delta_e_rate=r4(_mean(deltas)),
            confidence_interval_95=ci,
            per_service_drift=grouped("target_service"),
            per_disruption_drift=grouped("scenario_category"),
            trials=trials,
        )

    @classmethod
    def generate_markdown_report(cls, metrics: EnsembleBenchmarkMetrics) -> str:
        pct = lambda v: "n/a" if v is None else f"{v * 100:.2f}%"  # noqa: E731
        ci = metrics.confidence_interval_95
        lines = ["# Placement Drift Ensemble Summary", ""]
        if not metrics.is_experimental_evidence:
            lines += [f"> **{metrics.evidence_note}**", ""]
        lines += [
            "## 1. Summary",
            f"- **Execution mode**: {metrics.execution_mode.value}",
            f"- **Trials executed / completed / with measurable D_rate**: "
            f"{metrics.total_trials} / {metrics.successful_trials} / {metrics.measured_trials}",
            f"- **Mean D_rate**: {pct(metrics.mean_d_rate)}",
            f"- **Sample standard deviation**: {pct(metrics.std_dev_d_rate)}",
            f"- **95% confidence interval ({metrics.ci_method})**: "
            + (f"[{pct(ci[0])}, {pct(ci[1])}]" if ci else "n/a (fewer than 2 measured trials)"),
            f"- **Mean change in exposure rate**: {pct(metrics.mean_delta_e_rate)}",
            "",
            "## 2. Mean D_rate per target service",
            "| Service | Trials | Mean D_rate |",
            "| :--- | :---: | :---: |",
        ]
        for svc, rate in metrics.per_service_drift.items():
            count = sum(t.target_service == svc for t in metrics.trials if t.d_rate is not None)
            lines.append(f"| `{svc}` | {count} | {pct(rate)} |")
        lines += ["", "## 3. Mean D_rate per disruption type", "| Disruption | Trials | Mean D_rate |",
                  "| :--- | :---: | :---: |"]
        for cat, rate in metrics.per_disruption_drift.items():
            count = sum(t.scenario_category == cat for t in metrics.trials if t.d_rate is not None)
            lines.append(f"| **{cat}** | {count} | {pct(rate)} |")
        lines += ["", "## 4. Trials", "| Trial | Disruption | Target | D_rate | Delta E | Status |",
                  "| :--- | :--- | :--- | :---: | :---: | :--- |"]
        for t in metrics.trials:
            lines.append(
                f"| `{t.trial_id}` | {t.scenario_category} | `{t.target_service}` | {pct(t.d_rate)} | "
                f"{pct(t.delta_e_rate)} | {t.status.value} |"
            )
        return "\n".join(lines)
