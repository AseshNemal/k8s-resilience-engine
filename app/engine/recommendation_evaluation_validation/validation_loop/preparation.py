"""Making a copy of an application that can be created on a disposable test cluster, and saying honestly what could not be copied.

A disposable cluster starts empty. The application has to be put there before a change can be proven on it. This module turns the objects
discovered on the cluster the application runs on into portable manifests, and refuses to guess about anything it cannot carry over.

What it does:
* keeps only the kinds a placement test needs (Deployment, Service, ConfigMap, ServiceAccount) and forces every object into the test namespace;
* strips what belongs to the source cluster (uid, resourceVersion, status, owner references, cluster IPs, node ports, node names, the
  last-applied annotation, a revision counter), so the manifest can be created elsewhere;
* turns a LoadBalancer Service into a ClusterIP one (a test cluster has no cloud load balancer) and says so.

What it never does:
* it never reads or copies a Secret, and a pasted manifest containing one is refused. A Secret the application needs (an environment variable
  from a Secret, a Secret volume, an image-pull Secret) is listed as something the person must create in the test cluster themselves;
* it never assumes an external dependency exists. A PersistentVolumeClaim, an ExternalName Service, a node-name selector and a ConfigMap the
  source did not provide are each listed, and the copy is not deployable until each one is either resolved or removed by the person;
* it never writes anything. The caller shows the preview and, after an explicit approval, creates the objects.

The preview carries a digest. Creating anything requires the digest of the preview the person saw.
"""

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import yaml

ALLOWED_KINDS = ("Deployment", "Service", "ConfigMap", "ServiceAccount")
REFUSED_KINDS = ("Secret",)
HOSTNAME = "kubernetes.io/hostname"
LAST_APPLIED = "kubectl.kubernetes.io/last-applied-configuration"
REVISION = "deployment.kubernetes.io/revision"
COPIED_FROM = "resilience-validation/copied-from-namespace"
FORBIDDEN_TARGET_NAMESPACES = ("kube-system", "kube-public", "kube-node-lease", "default")

SERVER_METADATA = ("uid", "resourceVersion", "generation", "creationTimestamp", "deletionTimestamp", "selfLink", "managedFields",
                   "ownerReferences", "finalizers", "clusterName")
SERVICE_SPEC_DROP = ("clusterIP", "clusterIPs", "ipFamilies", "ipFamilyPolicy", "healthCheckNodePort", "loadBalancerIP",
                     "loadBalancerSourceRanges", "externalIPs", "externalTrafficPolicy", "internalTrafficPolicy", "loadBalancerClass")
SYSTEM_SERVICEACCOUNTS = ("default",)


class PreparationRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


@dataclass
class Missing:
    """Something the application needs that was not copied. ``blocking`` means the copy cannot be deployed until the person resolves it."""

    kind: str
    name: str
    used_by: str
    why: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "usedBy": self.used_by, "why": self.why, "blocking": self.blocking}


@dataclass
class PreparationPlan:
    source_namespace: str
    target_namespace: str
    manifests: list[dict[str, Any]]
    missing: list[Missing] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps({"target": self.target_namespace, "manifests": self.manifests,
                                          "missing": [m.to_dict() for m in self.missing]}, sort_keys=True).encode()).hexdigest()

    @property
    def deployable(self) -> bool:
        return bool(self.manifests) and not any(m.blocking for m in self.missing)

    def to_dict(self) -> dict[str, Any]:
        return {"sourceNamespace": self.source_namespace, "targetNamespace": self.target_namespace, "deployable": self.deployable,
                "objects": [{"kind": m["kind"], "name": m["metadata"]["name"]} for m in self.manifests],
                "missing": [m.to_dict() for m in self.missing], "notes": list(self.notes), "previewDigest": self.digest,
                "yaml": yaml.safe_dump_all(self.manifests, sort_keys=True)}


# -- sanitising ------------------------------------------------------------------------------------------------------

def _clean_metadata(metadata: dict[str, Any], target_namespace: str, source_namespace: str | None) -> dict[str, Any]:
    kept = {k: copy.deepcopy(v) for k, v in metadata.items() if k not in SERVER_METADATA and k != "namespace"}
    annotations = {k: v for k, v in (kept.get("annotations") or {}).items() if k not in (LAST_APPLIED, REVISION)}
    if annotations:
        kept["annotations"] = annotations
    else:
        kept.pop("annotations", None)
    kept["namespace"] = target_namespace
    if source_namespace:
        kept.setdefault("annotations", {})[COPIED_FROM] = source_namespace
    return kept


def _pod_references(pod_spec: dict[str, Any], owner: str, missing: list[Missing], configmaps: set[str]) -> None:
    """Record everything a pod template needs from outside itself that this copy does not carry."""
    for secret in pod_spec.get("imagePullSecrets") or []:
        missing.append(Missing("Secret", secret.get("name", "?"), owner, "an image-pull Secret: create it in the test cluster yourself"))
    for volume in pod_spec.get("volumes") or []:
        if "secret" in volume:
            missing.append(Missing("Secret", (volume["secret"] or {}).get("secretName", "?"), owner,
                                   "a Secret volume: create the Secret in the test cluster yourself"))
        if "persistentVolumeClaim" in volume:
            missing.append(Missing("PersistentVolumeClaim", volume["persistentVolumeClaim"].get("claimName", "?"), owner,
                                   "stored data is not copied; create the claim in the test cluster or remove the volume"))
        if "configMap" in volume:
            configmaps.add((volume["configMap"] or {}).get("name", ""))
        for source in ((volume.get("projected") or {}).get("sources") or []):
            if "secret" in source:
                missing.append(Missing("Secret", source["secret"].get("name", "?"), owner, "a projected Secret: create it in the test cluster yourself"))
            if "configMap" in source:
                configmaps.add(source["configMap"].get("name", ""))
    containers = list(pod_spec.get("containers") or []) + list(pod_spec.get("initContainers") or [])
    for container in containers:
        for env in container.get("env") or []:
            ref = env.get("valueFrom") or {}
            if "secretKeyRef" in ref:
                missing.append(Missing("Secret", ref["secretKeyRef"].get("name", "?"), owner,
                                       f"environment variable {env.get('name')} comes from a Secret: create it in the test cluster yourself"))
            if "configMapKeyRef" in ref:
                configmaps.add(ref["configMapKeyRef"].get("name", ""))
        for source in container.get("envFrom") or []:
            if "secretRef" in source:
                missing.append(Missing("Secret", source["secretRef"].get("name", "?"), owner, "environment from a Secret: create it in the test cluster yourself"))
            if "configMapRef" in source:
                configmaps.add(source["configMapRef"].get("name", ""))
    if (pod_spec.get("nodeSelector") or {}).get(HOSTNAME):
        missing.append(Missing("nodeSelector", pod_spec["nodeSelector"][HOSTNAME], owner,
                               "the pod is pinned to a node by name; node names differ in the test cluster, so remove the pin"))
    if pod_spec.get("nodeName"):
        missing.append(Missing("nodeName", pod_spec["nodeName"], owner, "the pod is bound to a node by name; remove it"))


def _clean_deployment(item: dict[str, Any], target: str, source: str | None, missing: list[Missing], configmaps: set[str]) -> dict[str, Any]:
    spec = copy.deepcopy(item.get("spec") or {})
    template = spec.get("template") or {}
    (template.get("metadata") or {}).pop("creationTimestamp", None)
    pod_spec = template.get("spec") or {}
    name = (item.get("metadata") or {}).get("name", "?")
    _pod_references(pod_spec, f"Deployment/{name}", missing, configmaps)
    pod_spec.pop("nodeName", None)
    spec.pop("paused", None)
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": _clean_metadata(item.get("metadata") or {}, target, source), "spec": spec}


def _clean_service(item: dict[str, Any], target: str, source: str | None, missing: list[Missing], notes: list[str]) -> dict[str, Any]:
    spec = {k: copy.deepcopy(v) for k, v in (item.get("spec") or {}).items() if k not in SERVICE_SPEC_DROP}
    name = (item.get("metadata") or {}).get("name", "?")
    if spec.get("type") == "ExternalName":
        missing.append(Missing("ExternalName", spec.get("externalName", "?"), f"Service/{name}",
                               "it points outside the cluster; the test cluster cannot be assumed to reach it"))
    if spec.get("type") == "LoadBalancer":
        spec["type"] = "ClusterIP"
        notes.append(f"Service {name} was a LoadBalancer; it is a ClusterIP in the test cluster (there is no cloud load balancer).")
    for port in spec.get("ports") or []:
        port.pop("nodePort", None)
    return {"apiVersion": "v1", "kind": "Service", "metadata": _clean_metadata(item.get("metadata") or {}, target, source), "spec": spec}


def _clean_configmap(item: dict[str, Any], target: str, source: str | None) -> dict[str, Any]:
    out = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": _clean_metadata(item.get("metadata") or {}, target, source)}
    for key in ("data", "binaryData", "immutable"):
        if key in item:
            out[key] = copy.deepcopy(item[key])
    return out


def _clean_serviceaccount(item: dict[str, Any], target: str, source: str | None, missing: list[Missing]) -> dict[str, Any]:
    name = (item.get("metadata") or {}).get("name", "?")
    for secret in item.get("secrets") or []:
        missing.append(Missing("Secret", secret.get("name", "?"), f"ServiceAccount/{name}", "a ServiceAccount token Secret is never copied",
                               blocking=False))
    out = {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": _clean_metadata(item.get("metadata") or {}, target, source)}
    if item.get("automountServiceAccountToken") is not None:
        out["automountServiceAccountToken"] = item["automountServiceAccountToken"]
    return out


def prepare(*, source_namespace: str, target_namespace: str, deployments: list[dict[str, Any]], services: list[dict[str, Any]],
            configmaps: list[dict[str, Any]], serviceaccounts: list[dict[str, Any]] = ()) -> PreparationPlan:
    """The portable copy of what was discovered, and what could not be carried over. Reads nothing, writes nothing."""
    if not target_namespace or target_namespace in FORBIDDEN_TARGET_NAMESPACES:
        raise PreparationRefused("TARGET_NAMESPACE_NOT_ALLOWED", "The copy goes into a namespace of its own, never a system namespace or 'default'.")
    missing: list[Missing] = []
    notes: list[str] = []
    needed_configmaps: set[str] = set()
    manifests: list[dict[str, Any]] = []
    for item in deployments:
        manifests.append(_clean_deployment(item, target_namespace, source_namespace, missing, needed_configmaps))
    for item in services:
        manifests.append(_clean_service(item, target_namespace, source_namespace, missing, notes))
    available = {(c.get("metadata") or {}).get("name"): c for c in configmaps}
    for name in sorted(n for n in needed_configmaps if n):
        item = available.get(name)
        if item is None:
            missing.append(Missing("ConfigMap", name, "a pod template", "the source did not provide it; create it in the test cluster or remove the reference"))
        else:
            manifests.append(_clean_configmap(item, target_namespace, source_namespace))
    used_accounts = {(((d.get("spec") or {}).get("template") or {}).get("spec") or {}).get("serviceAccountName") for d in deployments}
    for item in serviceaccounts:
        name = (item.get("metadata") or {}).get("name")
        if name in used_accounts and name not in SYSTEM_SERVICEACCOUNTS:
            manifests.append(_clean_serviceaccount(item, target_namespace, source_namespace, missing))
    seen: set[tuple] = set()
    unique = []
    for entry in missing:         # the same reference from several pods is one thing to resolve
        key = (entry.kind, entry.name, entry.used_by, entry.why)
        if key not in seen:
            seen.add(key)
            unique.append(entry)
    return PreparationPlan(source_namespace, target_namespace, manifests, unique, notes)


def parse_manual(yaml_text: str, target_namespace: str) -> PreparationPlan:
    """The fallback: manifests the person pasted. Only the allowed kinds, never a Secret, always forced into the test namespace."""
    try:
        documents = [d for d in yaml.safe_load_all(yaml_text or "") if d]
    except yaml.YAMLError as error:
        raise PreparationRefused("YAML_INVALID", f"The pasted text is not valid YAML: {error}") from error
    if not documents:
        raise PreparationRefused("YAML_EMPTY", "There is nothing to deploy in the pasted text.")
    by_kind: dict[str, list[dict[str, Any]]] = {kind: [] for kind in ALLOWED_KINDS}
    for document in documents:
        if not isinstance(document, dict) or "kind" not in document or not (document.get("metadata") or {}).get("name"):
            raise PreparationRefused("YAML_INVALID", "Every object needs a kind and a metadata.name.")
        kind = document["kind"]
        if kind in REFUSED_KINDS:
            raise PreparationRefused("SECRET_REFUSED", "A Secret is never deployed from here. Create it in the test cluster yourself, then remove it from this text.")
        if kind not in ALLOWED_KINDS:
            raise PreparationRefused("KIND_NOT_ALLOWED", f"{kind} cannot be deployed from here; only {', '.join(ALLOWED_KINDS)} can.")
        by_kind[kind].append(document)
    plan = prepare(source_namespace="(pasted)", target_namespace=target_namespace, deployments=by_kind["Deployment"], services=by_kind["Service"],
                   configmaps=by_kind["ConfigMap"], serviceaccounts=by_kind["ServiceAccount"])
    present = {(m["kind"], m["metadata"]["name"]) for m in plan.manifests}
    for item in by_kind["ConfigMap"]:         # a pasted ConfigMap is deployed as given even if no pod refers to it
        if ("ConfigMap", item["metadata"]["name"]) not in present:
            plan.manifests.append(_clean_configmap(item, target_namespace, "(pasted)"))
    for item in by_kind["ServiceAccount"]:
        if ("ServiceAccount", item["metadata"]["name"]) not in {(m["kind"], m["metadata"]["name"]) for m in plan.manifests}:
            plan.manifests.append(_clean_serviceaccount(item, target_namespace, "(pasted)", plan.missing))
    return plan
