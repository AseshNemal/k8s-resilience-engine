"""Overview discovery across every namespace of the connected cluster (Component 2).

``discover_all()`` finds the namespaces that hold workloads and runs the existing ``discover()`` for each
one, so every namespace's result is exactly what the single-namespace route returns; nothing here
re-implements topology resolution. It adds only the cluster-wide pieces: which namespaces, how many worker
nodes (counted once, not per namespace) and the totals.

System namespaces are left out by default and named in ``excluded_namespaces``, so nothing is hidden
silently. Like ``discover()`` it either returns real evidence or raises ``DiscoveryError``; it never
returns a partial overview when the cluster cannot be read.
"""

import logging
from datetime import datetime, timezone
from typing import Literal, Protocol

from app.engine.deployment_topology_modeling import discovery
from app.engine.deployment_topology_modeling.discovery import (
    ClusterInfo,
    DiscoveryError,
    DiscoveryResult,
    discover,
)
from app.integrations.kubernetes.client import ClusterAccessError
from app.shared.schemas.base import APIModel

logger = logging.getLogger(__name__)

# Namespaces that hold the platform, not an application. Excluded unless ``include_system`` is set.
SYSTEM_NAMESPACES = ("kube-system", "kube-public", "kube-node-lease", "local-path-storage")


class OverviewSource(discovery.ClusterSource, Protocol):
    def list_workload_namespaces(self) -> list[str]: ...


class OverviewTotals(APIModel):
    worker_nodes: int
    services: int
    unresolved: int


class AllNamespacesDiscovery(APIModel):
    status: Literal["ok", "empty", "partial"]
    cluster: ClusterInfo
    captured_at: str
    namespaces: list[DiscoveryResult]
    excluded_namespaces: list[str]
    totals: OverviewTotals


def discover_all(source: OverviewSource | None = None, include_system: bool = False) -> AllNamespacesDiscovery:
    source = source or discovery.get_cluster_source()
    try:
        names = sorted(set(source.list_workload_namespaces()))
        nodes = source.list_nodes()
        fingerprint = source.connection_fingerprint()
    except ClusterAccessError as error:
        logger.warning("Cluster overview failed (%s): %s", type(error).__name__, error)
        raise discovery._classify(error) from None
    except DiscoveryError:
        raise
    except Exception:
        # The exception text may contain request details; keep it in the server log only.
        logger.exception("Unexpected failure while listing the cluster's namespaces")
        raise DiscoveryError("DISCOVERY_FAILED", "The cluster returned an unexpected error during discovery.",
                             http_status=502, retryable=True) from None

    excluded = [] if include_system else [name for name in names if name in SYSTEM_NAMESPACES]
    results = [discover(name, source) for name in names if name not in excluded]

    statuses = {result.status for result in results}
    if not results or statuses == {"empty"}:
        status = "empty"
    elif "partial" in statuses:
        status = "partial"
    else:
        status = "ok"

    return AllNamespacesDiscovery(
        status=status,
        cluster=ClusterInfo(connection_fingerprint=fingerprint, identity_verified=False),
        captured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        namespaces=results,
        excluded_namespaces=excluded,
        totals=OverviewTotals(
            worker_nodes=sum(1 for node in nodes if not node.control_plane),
            services=sum(len(result.graph.services) for result in results),
            unresolved=sum(len(result.unresolved) for result in results),
        ),
    )
