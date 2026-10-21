"""Live-cluster data classes. They are defined by the Kubernetes integration and re-exported here.

Member 2's parser and Service resolver keep importing them from this module. The single definition
lives in app/integrations/kubernetes/models.py so the REST client and the live-state parser share
one shape, and carries the union of both sides' fields (ready, schedulable, phase, owner_uid, ...).
"""

from app.integrations.kubernetes.models import ClusterDeployment, ClusterNode, ClusterPod

__all__ = ["ClusterDeployment", "ClusterNode", "ClusterPod"]
