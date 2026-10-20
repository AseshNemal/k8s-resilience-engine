from typing import Dict, List, Any
import datetime
import hashlib
import json
from app.shared.schemas.deployment import (
    DeploymentGraph,
    NodeModel,
    PodModel,
    ServicePlacement,
    ClusterMetadata,
    ValidationMetrics,
    DeclaredPlacementIntent,
    TopologySpreadConstraintModel,
)

UNSCHEDULED_NODE = "unscheduled"


class DeploymentGraphBuilder:
    """Transforms analysed live state into the shared DeploymentGraph contract."""

    def __init__(self):
        self.node_models: List[NodeModel] = []
        self.service_models: List[ServicePlacement] = []
        self.node_zone_map: Dict[str, str | None] = {}

    def attach_nodes(self, nodes: List[Any]) -> None:
        """Attaches worker nodes (ClusterNode objects)."""
        for n in nodes:
            self.node_zone_map[n.name] = n.zone
            self.node_models.append(NodeModel(
                name=n.name,
                zone=n.zone,
                cpu_available=self._parse_cpu_value(str(n.cpu_available)),
                memory_available=self._parse_memory_value(str(n.memory_available)),
                labels=n.labels or {},
                ready=getattr(n, "ready", None),
                schedulable=getattr(n, "schedulable", True),
            ))

    @staticmethod
    def _intent_model(intent: Dict[str, Any], svc: Dict[str, Any]) -> DeclaredPlacementIntent:
        return DeclaredPlacementIntent(
            declared_replicas=intent.get("expected_replicas", svc.get("declaredReplicas", 1)),
            selector=intent.get("selector", {}),
            readiness_probe_configured=intent.get("readiness_probe_configured", False),
            node_affinity_configured=intent.get("has_node_affinity", False),
            pod_anti_affinity_configured=intent.get("pod_anti_affinity_configured", False),
            pod_affinity_configured=intent.get("pod_affinity_configured", False),
            node_selector=intent.get("node_selector", {}),
            tolerations=intent.get("tolerations", []),
            selector_match_expressions=intent.get("selector_match_expressions", []),
            node_affinity_rules=intent.get("node_affinity_rules", {}),
            pod_affinity_rules=intent.get("pod_affinity_rules", {}),
            pod_anti_affinity_rules=intent.get("pod_anti_affinity_rules", {}),
            topology_spread_constraints=[
                TopologySpreadConstraintModel.model_validate(constraint)
                for constraint in intent.get("spread_constraints", [])
            ],
            resource_requests=intent.get("resource_requests", []),
            resource_limits=intent.get("resource_limits_by_container", []),
        )

    def attach_services(
        self,
        analyzed_services: List[Dict[str, Any]],
        drift_reports: Dict[str, Dict[str, Any]],
        service_intents: Dict[str, Dict[str, Any]] | None = None,
    ) -> None:
        """Attaches analysed services together with their drift findings."""
        for svc in analyzed_services:
            pod_models = []
            for p in svc["pods"]:
                pod_node = p.get("node") or UNSCHEDULED_NODE
                pod_models.append(PodModel(
                    name=p.get("name", "unknown"),
                    node=pod_node,
                    zone=self.node_zone_map.get(pod_node),
                    ready=p.get("ready", False),
                    phase=p.get("phase"),
                    restart_count=p.get("restartCount", 0),
                    namespace=p.get("namespace", "default"),
                    owner_kind=p.get("ownerKind"),
                    owner_name=p.get("ownerName"),
                    owner_uid=p.get("ownerUid"),
                ))

            drift_data = drift_reports.get(svc.get("name"), {"drift_detected": False, "drift_reasons": []})
            intent = (service_intents or {}).get(svc.get("name"))

            self.service_models.append(ServicePlacement(
                name=svc.get("name", "unknown"),
                namespace=svc.get("namespace", "default"),
                declared_replicas=svc.get("declaredReplicas", 1),
                ready_replicas=svc.get("readyReplicas", 0),
                replica_concentration=svc.get("replicaConcentration"),
                concentrated=svc.get("concentrated"),
                drift_detected=drift_data["drift_detected"],
                drift_reasons=drift_data["drift_reasons"],
                pods=pod_models,
                hosting_nodes=sorted({p.node for p in pod_models if p.node != UNSCHEDULED_NODE}),
                exposed_failure_domains=svc.get("exposedNodes", []),
                declared_intent=self._intent_model(intent, svc) if intent is not None else None,
                deployment_names=svc.get("deploymentNames", []),
            ))

    def _placement_fingerprint(self) -> str:
        """Same placement -> same snapshot id, so snapshots are reproducible and comparable."""
        placement = sorted(
            (s.namespace, s.name, p.name, p.node, p.ready)
            for s in self.service_models for p in s.pods
        )
        nodes = sorted((n.name, n.ready) for n in self.node_models)
        digest = hashlib.sha256(json.dumps([placement, nodes]).encode("utf-8")).hexdigest()
        return digest[:12]

    def build(self, runtime_overhead_ms: float = 0.0) -> DeploymentGraph:
        """Accuracy metrics stay None until measured against independent ground truth."""
        return DeploymentGraph(
            snapshot_id=f"snap-{self._placement_fingerprint()}",
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            cluster=ClusterMetadata(
                failure_domain_type="kubernetes.io/hostname",
                node_names=[n.name for n in self.node_models],
            ),
            validation=ValidationMetrics(runtime_overhead_ms=runtime_overhead_ms),
            nodes=self.node_models,
            services=self.service_models,
        )

    def _parse_cpu_value(self, cpu_str: str) -> float | None:
        if not cpu_str or cpu_str.lower() in {"none", "null"}:
            return None
        if cpu_str.endswith("m"):
            return round(float(cpu_str[:-1]) / 1000.0, 4)
        try:
            return float(cpu_str)
        except ValueError:
            return None

    def _parse_memory_value(self, mem_str: str) -> float | None:
        """Returns GiB. Plain numbers are bytes (the live parser emits bytes)."""
        if not mem_str or mem_str.lower() in {"none", "null"}:
            return None
        binary = {"Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3, "Ti": 1024 ** 4}
        decimal = {"k": 1000, "M": 1000 ** 2, "G": 1000 ** 3, "T": 1000 ** 4}
        try:
            for suffix, factor in binary.items():
                if mem_str.endswith(suffix):
                    return round(float(mem_str[:-2]) * factor / 1024 ** 3, 4)
            for suffix, factor in decimal.items():
                if mem_str.endswith(suffix):
                    return round(float(mem_str[:-1]) * factor / 1024 ** 3, 4)
            return round(float(mem_str) / 1024 ** 3, 4)
        except ValueError:
            return None
