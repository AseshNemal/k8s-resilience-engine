"""Runs live placement-drift experiments and records every observation.

One run = reset -> T0 observe -> real disruption -> wait -> T1 observe -> measure -> undo.

T0 and T1 are observed with the Component 2 collector (service.analyze_live_topology), so the
experiment measures exactly what the DeploymentGraph reports. Metrics use placement_metrics:
  D_rate  = share of services whose exposure status changed between T0 and T1
  Delta_E = exposure rate at T1 - exposure rate at T0
"""
import atexit
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.engine.deployment_topology_modeling import placement_metrics as pm
from experiments.placement_drift.exceptions import ScenarioExecutionError
from experiments.placement_drift.live.kubectl import Kubectl
from experiments.placement_drift.live.scenarios import LiveScenario, RunContext
from experiments.placement_drift.live.workloads import STANDIN, Workload

EXPERIMENT_LABEL = "experiment"  # every experiment pod (workload and filler) carries this label key


def _statuses(analysis) -> dict[str, pm.ExposureStatus]:
    return {s.service_name: s.exposure_status for s in analysis.report.services}


def _placement(analysis) -> dict[str, dict[str, Any]]:
    return {
        s.name: {
            "ready_replicas": s.ready_replicas,
            "concentrated": s.concentrated,
            "replica_concentration": s.replica_concentration,
            "exposed_failure_domains": s.exposed_failure_domains,
            "pods": sorted((p.name, p.node, p.ready) for p in s.pods),
        }
        for s in analysis.graph.services
    }


def _moved_pods(t0, t1) -> int:
    before = {p.name: p.node for s in t0.graph.services for p in s.pods}
    after = {p.name: p.node for s in t1.graph.services for p in s.pods}
    return sum(1 for name, node in before.items() if name in after and after[name] != node)


@dataclass
class RunRecord:
    scenario_id: str
    config: str
    repetition: int
    started_at: str
    action: dict[str, Any]
    stable_after_disruption: bool
    wait_seconds: float | None
    d_rate: float | None
    exposure_rate_t0: float | None
    exposure_rate_t1: float | None
    delta_e_rate: float | None
    compliance_rate_t1: float | None
    changed_services: list[str]
    transitions: dict[str, str]
    target_status_t0: str
    target_status_t1: str
    moved_pods: int
    observation_ms_t0: float | None
    observation_ms_t1: float | None
    t0: dict[str, Any] = field(default_factory=dict)
    t1: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    workload: str = STANDIN.name
    target_service: str = STANDIN.target_service


class LiveExperimentRunner:
    def __init__(
        self,
        kube: Kubectl,
        configs: dict[str, str],
        deployments: list[str],
        results_dir: Path,
        observe: Callable[[str, str], Any] | None = None,
        observe_nodes: Callable[[], list[Any]] | None = None,
        disruption_timeout_s: int = 90,
        settle_s: float = 3.0,
        workload: Workload = STANDIN,
    ):
        self.kube = kube
        self.workload = workload
        self.configs = configs
        self.deployments = deployments
        self.results_dir = results_dir
        self.disruption_timeout_s = disruption_timeout_s
        self.settle_s = settle_s
        if observe is None or observe_nodes is None:
            # The collector reads the kubeconfig's current context; give it a kubeconfig that holds only the
            # experiment context, so T0/T1 are observed on the same cluster kubectl changes.
            pinned = kube.write_pinned_kubeconfig()
            atexit.register(pinned.unlink, missing_ok=True)
            os.environ["KUBECONFIG"] = str(pinned)
        if observe is None:
            from app.engine.deployment_topology_modeling.service import analyze_live_topology
            observe = lambda namespace, manifest: analyze_live_topology(namespace, manifest)  # noqa: E731
        if observe_nodes is None:
            from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import get_cluster_nodes
            observe_nodes = get_cluster_nodes
        self.observe = observe
        self.observe_nodes = observe_nodes

    def reset(self, manifest: str) -> None:
        """Every run starts from a fresh deployment on uncordoned nodes."""
        for node in self.kube.worker_nodes():
            self.kube.uncordon(node)
        self.kube.delete_experiment_workloads()
        self.kube.wait_until_no_pods(EXPERIMENT_LABEL)
        self.kube.apply(manifest)
        self.kube.wait_until_stable(self.deployments)
        time.sleep(self.settle_s)

    def run_once(self, scenario: LiveScenario, config: str, repetition: int) -> RunRecord:
        manifest = self.configs[config]
        started = datetime.now(timezone.utc).isoformat()
        self.reset(manifest)

        t0 = self.observe(self.kube.namespace, manifest)
        ctx = RunContext(kube=self.kube, t0=t0, deployments=self.deployments, observe_nodes=self.observe_nodes,
                         workload=self.workload)
        error = None
        try:
            action = scenario.disrupt(ctx)
            try:
                wait_s = self.kube.wait_until_stable(self.deployments, timeout_s=self.disruption_timeout_s)
                stable = True
            except ScenarioExecutionError as exc:
                # Not settling is itself an observation (e.g. pods Pending under a spread rule).
                wait_s, stable, error = None, False, str(exc)
            time.sleep(self.settle_s)
            t1 = self.observe(self.kube.namespace, manifest)
        finally:
            scenario.undo(ctx)

        drift = pm.placement_drift(_statuses(t0), _statuses(t1))
        e0, e1 = t0.report.exposure_rate, t1.report.exposure_rate
        return RunRecord(
            scenario_id=scenario.scenario_id,
            config=config,
            repetition=repetition,
            started_at=started,
            action=action,
            stable_after_disruption=stable,
            wait_seconds=wait_s,
            d_rate=drift.rate,
            exposure_rate_t0=e0,
            exposure_rate_t1=e1,
            delta_e_rate=None if e0 is None or e1 is None else round(e1 - e0, 4),
            compliance_rate_t1=t1.report.constraint_compliance_rate,
            changed_services=drift.changed_services,
            transitions=drift.transitions,
            target_status_t0=_statuses(t0)[self.workload.target_service].value,
            target_status_t1=_statuses(t1)[self.workload.target_service].value,
            moved_pods=_moved_pods(t0, t1),
            observation_ms_t0=t0.graph.validation.runtime_overhead_ms,
            observation_ms_t1=t1.graph.validation.runtime_overhead_ms,
            t0=_placement(t0),
            t1=_placement(t1),
            error=error,
            workload=self.workload.name,
            target_service=self.workload.target_service,
        )

    def _run_and_store(self, scenario: LiveScenario, config: str, repetition: int) -> RunRecord | None:
        """Runs one condition and writes its JSON; a failed run is written too, never dropped."""
        path = self.results_dir / f"{scenario.scenario_id}__{config}__run{repetition}.json"
        try:
            record = self.run_once(scenario, config, repetition)
        except Exception as exc:  # recorded, never silently dropped
            path.write_text(json.dumps({
                "scenario_id": scenario.scenario_id, "config": config,
                "repetition": repetition, "failed": True, "error": repr(exc),
            }, indent=2), encoding="utf-8")
            return None
        path.write_text(json.dumps(record.__dict__, indent=2, default=str), encoding="utf-8")
        return record

    def run(self, scenarios: list[LiveScenario], configs: list[str], repetitions: int,
            on_record: Callable[[RunRecord | None], None] | None = None) -> list[RunRecord]:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        conditions = [(s, c, r) for r in range(1, repetitions + 1) for c in configs for s in scenarios]
        records = []
        try:
            for scenario, config, repetition in conditions:
                record = self._run_and_store(scenario, config, repetition)
                if record is not None:
                    records.append(record)
                if on_record:
                    on_record(record)
        finally:
            for node in self.kube.worker_nodes():
                self.kube.uncordon(node)
        return records
