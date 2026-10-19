from typing import Dict, List, Any
import datetime
import uuid
from app.shared.schemas.deployment import (
    DeploymentGraph,
    NodeModel,
    PodModel,
    ServicePlacement,
    ClusterMetadata,
    ValidationMetrics,
)

_BYTES_PER_GIB = 1024 ** 3
_MEMORY_UNITS = {
    "Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3, "Ti": 1024 ** 4,
    "k": 1000, "K": 1000, "M": 1000 ** 2, "G": 1000 ** 3, "T": 1000 ** 4,
}


class DeploymentGraphBuilder:
    """Transforms raw analytics and state data into the strict JSON DeploymentGraph.

    Anything the cluster did not report stays ``None`` in the graph. No zone, capacity, node or
    accuracy value is ever invented to fill a gap.
    """

    def __init__(self):
        self.node_models: List[NodeModel] = []
        self.service_models: List[ServicePlacement] = []
        self.node_zone_map: Dict[str, str | None] = {}

    def attach_nodes(self, nodes: List[Any]) -> None:
        """Parses and attaches worker nodes into the graph."""
        for n in nodes:
            self.node_zone_map[n.name] = n.zone
            self.node_models.append(NodeModel(
                name=n.name,
                zone=n.zone,
                cpu_available=self._parse_cpu_value(n.cpu_available),
                memory_available=self._parse_memory_value(n.memory_available),
            ))

    def attach_services(self, analyzed_services: List[Dict[str, Any]], drift_reports: Dict[str, Dict[str, Any]]) -> None:
        """Parses and attaches workloads along with drift reports into the graph.

        Every pod passed in must already have an assigned node; pods without one are reported by the
        discovery layer as unresolved and never reach this method.
        """
        for svc in analyzed_services:
            if not svc.get("pods"):
                continue

            pod_models = [
                PodModel(
                    name=p["name"],
                    node=p["node"],
                    zone=self.node_zone_map.get(p["node"]),
                    ready=p.get("ready", False),
                    restart_count=p.get("restart_count"),
                )
                for p in svc["pods"]
            ]

            drift_data = drift_reports.get(svc.get("name"), {"drift_detected": False, "drift_reasons": []})

            self.service_models.append(ServicePlacement(
                name=svc.get("name", "unknown"),
                declared_replicas=svc.get("declaredReplicas", 1),
                ready_replicas=svc.get("readyReplicas", 0),
                replica_concentration=svc.get("replicaConcentration"),
                drift_detected=drift_data["drift_detected"],
                drift_reasons=drift_data["drift_reasons"],
                pods=pod_models,
            ))

    def build(self, runtime_overhead_ms: float = 0.0) -> DeploymentGraph:
        """Generates the final strict Graph object.

        Only the measured runtime overhead is reported. The three accuracy fields stay ``None``
        because no ground-truth validation has been run against this snapshot.
        """
        node_names = [n.name for n in self.node_models]

        return DeploymentGraph(
            snapshot_id=f"snap-{uuid.uuid4().hex[:6]}",
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            cluster=ClusterMetadata(
                failure_domain_type="kubernetes.io/hostname",
                node_names=node_names,
            ),
            validation=ValidationMetrics(runtime_overhead_ms=runtime_overhead_ms),
            nodes=self.node_models,
            services=self.service_models,
        )

    @staticmethod
    def _parse_cpu_value(cpu: str | None) -> float | None:
        """CPU cores from a Kubernetes quantity ("2", "1500m"); None if absent or unreadable."""
        if not cpu:
            return None
        try:
            if cpu.endswith("m"):
                return round(float(cpu[:-1]) / 1000.0, 2)
            return float(cpu)
        except ValueError:
            return None

    @staticmethod
    def _parse_memory_value(memory: str | None) -> float | None:
        """GiB from a Kubernetes quantity ("4Gi", "8388608Ki", bytes); None if absent or unreadable."""
        if not memory:
            return None
        try:
            for suffix, factor in _MEMORY_UNITS.items():
                if memory.endswith(suffix):
                    return round(float(memory[:-len(suffix)]) * factor / _BYTES_PER_GIB, 2)
            return round(float(memory) / _BYTES_PER_GIB, 2)
        except ValueError:
            return None
