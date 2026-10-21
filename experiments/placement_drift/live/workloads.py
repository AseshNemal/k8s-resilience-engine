"""The workloads the live placement-drift experiments can run on.

A workload says where the experiment runs (namespace), which Deployments make it up, which service the
scenarios disrupt, and which configuration files (A: no placement rule, B: topology spread) are applied.
Everything else (scenarios, safety rules, measurements) is the same for every workload.

The target is named twice because Kubernetes names the two objects separately: the Service name finds
the target's pods in the observed DeploymentGraph and its exposure status, the Deployment name is what
kubectl scales and restarts. For the stand-in workload they are equal; Bookinfo calls its Deployment
reviews-v1 for Service reviews.
"""
from dataclasses import dataclass
from pathlib import Path

from experiments.placement_drift.live import bookinfo_workloads as bookinfo
from experiments.placement_drift.live import generate_workloads as standin

DEPLOY_DIR = Path(__file__).resolve().parents[3] / "deploy" / "experiments"


@dataclass(frozen=True)
class Workload:
    name: str
    namespace: str
    deployments: tuple[str, ...]   # every Deployment the runner waits for
    target_service: str            # Service name: pods and exposure status in the DeploymentGraph
    target_deployment: str         # Deployment name: what kubectl scales and restarts
    target_replicas: int
    configs: dict[str, str]        # configuration name -> manifest path
    results_prefix: str = ""       # prepended to the default timestamped results folder

    @property
    def target_selector(self) -> str:
        """Label selector of the target's pods (both workloads label pods with app=<service>)."""
        return f"app={self.target_service}"


STANDIN = Workload(
    name="standin",
    namespace=standin.NAMESPACE,
    deployments=tuple(standin.SERVICES),
    target_service="paymentservice",
    target_deployment="paymentservice",
    target_replicas=standin.REPLICAS,
    configs={
        "A-no-rule": str(DEPLOY_DIR / "placement-drift" / "config-a-no-rule.yaml"),
        "B-spread": str(DEPLOY_DIR / "placement-drift" / "config-b-topology-spread.yaml"),
    },
)

# The real-application check: the team's Bookinfo benchmark (v1) in its own namespace, reviews as the target
# (the largest service, and productpage depends on it). Every service is still observed in every run.
BOOKINFO = Workload(
    name="bookinfo",
    namespace=bookinfo.NAMESPACE,
    deployments=tuple(bookinfo.SERVICES.values()),
    target_service="reviews",
    target_deployment=bookinfo.SERVICES["reviews"],
    target_replicas=bookinfo.REPLICAS,
    configs={
        "A-no-rule": str(DEPLOY_DIR / "placement-drift-bookinfo" / "config-a-no-rule.yaml"),
        "B-spread": str(DEPLOY_DIR / "placement-drift-bookinfo" / "config-b-topology-spread.yaml"),
    },
    results_prefix="bookinfo-",
)

WORKLOADS = {w.name: w for w in (STANDIN, BOOKINFO)}
