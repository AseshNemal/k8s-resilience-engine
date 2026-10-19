"""Builders for discovery results used by downstream-component tests."""

from app.engine.deployment_topology_modeling.discovery import (
    ClusterInfo,
    DiscoveryResult,
    DiscoveryWarning,
    ServiceEvidence,
    UnresolvedItem,
)
from app.shared.schemas.deployment import DeploymentGraph


def discovery_for(
    deployment: DeploymentGraph,
    *,
    status: str = "ok",
    incomplete: dict[str, list[str]] | None = None,
    unresolved: list[UnresolvedItem] | None = None,
    warnings: list[DiscoveryWarning] | None = None,
    namespace: str = "default",
) -> DiscoveryResult:
    """A discovery result describing ``deployment``; ``incomplete`` maps service -> reasons."""
    incomplete = incomplete or {}
    evidence = {
        service.name: ServiceEvidence(
            complete=service.name not in incomplete,
            reasons=incomplete.get(service.name, []),
        )
        for service in deployment.services
    }
    for name, reasons in incomplete.items():
        evidence.setdefault(name, ServiceEvidence(complete=False, reasons=reasons))
    return DiscoveryResult(
        status=status,
        cluster=ClusterInfo(connection_fingerprint="test-connection", identity_verified=False),
        namespace=namespace,
        captured_at="2026-01-01T00:00:00Z",
        warnings=warnings or [],
        unresolved=unresolved or [],
        service_evidence=evidence,
        graph=deployment,
    )
