"""Which cluster a validation run may write to and break, and how that is proven.

``TestTarget(name, "test")`` is only a label; anyone can type it. A real executor therefore does not accept a label. It
reads the identity of the cluster it is actually connected to and compares it with a ``DisposableClusterDesignation``
that a person wrote down, and only a cluster that matches becomes a ``TestTarget``.

What identifies a cluster. The UID of its ``kube-system`` namespace: it is generated when the cluster is created, it is
unique to that cluster, and it does not change when the connection settings are renamed, copied or pointed at another address. A
context name or an API address can be edited to say anything, so they are checked as well but never trusted alone. A
designation without a UID designates nothing.

The research cluster. The Docker Desktop cluster the rest of the project is developed and demonstrated against is never a
validation target by default, even if someone designates it: a controlled node failure there would break the
environment the other components depend on. It is recognised by its context and node names (configurable below) and
refused unless a ``ResearchClusterAuthorization``, a separate and explicit act naming that cluster's UID, says
otherwise. The authorization lifts only that refusal; the cluster must still be designated.

Pure: this module reads no cluster. The gateway that does supplies the ``ClusterIdentity``.
"""

from dataclasses import dataclass

from app.engine.recommendation_evaluation_validation.validation.models import (
    TEST_ENVIRONMENT,
    TestTarget,
    ValidationRefusedError,
)

# Namespaces that belong to Kubernetes itself; an experiment never lives there.
SYSTEM_NAMESPACES = frozenset({"kube-system", "kube-public", "kube-node-lease"})

# How the normal Docker Desktop research cluster presents itself (single-node and multi-node "kind" mode).
RESEARCH_CONTEXTS = frozenset({"docker-desktop", "docker-for-desktop"})
RESEARCH_NODE_NAMES = frozenset({"docker-desktop"})
RESEARCH_NODE_PREFIXES = ("desktop-control-plane", "desktop-worker")


@dataclass(frozen=True)
class ClusterIdentity:
    """What the cluster a gateway is connected to says about itself. ``cluster_uid`` None means it could not be read."""

    context: str | None
    api_server: str
    cluster_uid: str | None
    node_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class DisposableClusterDesignation:
    """A person's statement: this cluster, and only this cluster, may be written to and broken by a validation run."""

    name: str
    api_server: str
    cluster_uid: str
    namespace: str                          # the one namespace the run may write to
    designated_by: str
    context: str | None = None              # optional extra pins; when given they must match too
    node_names: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ResearchClusterAuthorization:
    """A separate, explicit go-ahead to treat the normal research cluster as a validation target, for that cluster only."""

    confirmed_by: str
    cluster_uid: str
    reason: str


def _address(api_server: str) -> str:
    return (api_server or "").strip().rstrip("/").lower()


def _looks_like_research_cluster(identity: ClusterIdentity, contexts: frozenset[str], node_names: frozenset[str],
                                 node_prefixes: tuple[str, ...]) -> bool:
    if identity.context in contexts:
        return True
    return any(node in node_names or node.startswith(node_prefixes) for node in identity.node_names)


def _research_cluster_authorized(identity: ClusterIdentity, override: ResearchClusterAuthorization | None) -> bool:
    return (override is not None and bool((override.confirmed_by or "").strip()) and bool((override.reason or "").strip())
            and identity.cluster_uid is not None and override.cluster_uid == identity.cluster_uid)


def verify_cluster(
    identity: ClusterIdentity,
    designation: DisposableClusterDesignation,
    *,
    research_override: ResearchClusterAuthorization | None = None,
    research_contexts: frozenset[str] = RESEARCH_CONTEXTS,
    research_node_names: frozenset[str] = RESEARCH_NODE_NAMES,
    research_node_prefixes: tuple[str, ...] = RESEARCH_NODE_PREFIXES,
) -> TestTarget:
    """The ``TestTarget`` for the connected cluster, or a refusal that says which check failed. Reads nothing."""
    if (not (designation.cluster_uid or "").strip() or not (designation.designated_by or "").strip()
            or not (designation.name or "").strip()):
        raise ValidationRefusedError("CLUSTER_NOT_DESIGNATED", "A designation must name the cluster, say who designated "
                                     "it, and pin the UID of its kube-system namespace.")
    if not (designation.namespace or "").strip() or designation.namespace in SYSTEM_NAMESPACES:
        raise ValidationRefusedError("CLUSTER_NOT_DESIGNATED", "The designation must name a namespace of its own for the "
                                     "experiment, never a Kubernetes system namespace.")
    if identity.cluster_uid is None:
        raise ValidationRefusedError("CLUSTER_IDENTITY_UNVERIFIED", "The identity of the connected cluster could not be read "
                                     "(the UID of its kube-system namespace), so it is not assumed to be the designated one.")

    if (_looks_like_research_cluster(identity, research_contexts, research_node_names, research_node_prefixes)
            and not _research_cluster_authorized(identity, research_override)):
        raise ValidationRefusedError("RESEARCH_CLUSTER_REFUSED", "This is the normal research cluster. A validation run "
                                     "breaks a node on purpose, so it needs a ResearchClusterAuthorization naming this "
                                     "cluster's UID; it must be separately authorized, and designating it is not enough.")

    mismatches = []
    if identity.cluster_uid != designation.cluster_uid:
        mismatches.append("the kube-system namespace UID")
    if _address(identity.api_server) != _address(designation.api_server):
        mismatches.append("the API server address")
    if designation.context is not None and identity.context != designation.context:
        mismatches.append("the connection context")
    if designation.node_names is not None and sorted(identity.node_names) != sorted(designation.node_names):
        mismatches.append("the node set")
    if mismatches:
        raise ValidationRefusedError("CLUSTER_IDENTITY_MISMATCH", "The connected cluster is not the designated one: "
                                     + ", ".join(mismatches) + " differ(s).")
    return TestTarget(designation.name, TEST_ENVIRONMENT)
