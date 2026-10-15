"""
Compare T0 (Before Disruption) vs T1 (After Disruption) Workload Placement.
Component: Component 2 - Deployment Topology Modeling
"""
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from experiments.placement_drift.snapshot_repository import SnapshotRepository
from experiments.placement_drift.disruption_orchestrator import DisruptionOrchestrator


def compare(scenario_id: str = "07_node_drain_eviction"):
    repo = SnapshotRepository()
    orch = DisruptionOrchestrator(repository=repo)
    result = orch.run_scenario(scenario_id)

    t0 = repo.get(scenario_id, "T0")
    t1 = repo.get(scenario_id, "T1")

    if not t0 or not t1:
        print(f"Error: Snapshots for {scenario_id} not found.")
        return

    print("=" * 72)
    print(f"SCENARIO COMPARISON: {scenario_id}")
    print("=" * 72)
    print(f"{'POD NAME':<16} | {'T0 (BEFORE FAULT)':<18} | {'T1 (AFTER FAULT)':<18} | {'STATUS'}")
    print("-" * 72)

    t1_map = {p.pod_name: p.node_name for p in t1.pods}
    for p in t0.pods:
        src = p.node_name
        dst = t1_map.get(p.pod_name, "TERMINATED")
        status = "--> [DRIFTED!]" if src != dst else "(Steady)"
        print(f"{p.pod_name:<16} | {src:<18} | {dst:<18} | {status}")

    print("=" * 72)
    print(f"T0 SHA-256 Fingerprint: {t0.sha256_hash}")
    print(f"T1 SHA-256 Fingerprint: {t1.sha256_hash}")
    print(f"Calculated Drift Rate (D_rate): {result.d_rate * 100:.2f}%")
    print(f"Net Exposure Delta (Delta_E):   {result.delta_e_rate * 100:+.2f}%")
    print("=" * 72)


if __name__ == "__main__":
    scenario = sys.argv[1] if len(sys.argv) > 1 else "07_node_drain_eviction"
    compare(scenario)
