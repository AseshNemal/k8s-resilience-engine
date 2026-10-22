"""Kubernetes YAML for an approved-eligible recommendation: a minimal Deployment patch, never an applied change.

What is produced. One strategic-merge patch of the backing Deployment per changed service, adding exactly what the
feasibility check verified and nothing else:

* TOPOLOGY_SPREAD: ``topologySpreadConstraints`` on ``kubernetes.io/hostname``, ``maxSkew`` as modelled,
  ``whenUnsatisfiable: DoNotSchedule``, selecting the Deployment's own pod labels;
* POD_ANTI_AFFINITY: a required ``podAntiAffinity`` term on ``kubernetes.io/hostname`` selecting the Deployment's own
  pods. Kubernetes treats that list as atomic, so a patch REPLACES it: a Deployment that already has required terms is
  refused instead of having them silently dropped;
* REPLICA_INCREASE: ``spec.replicas`` raised by the verified amount, together with the spread constraint that keeps the
  added replica off the node of the existing one (without it the scheduler may co-locate them);
* a COMBINATION is one such patch per member Deployment, in a fixed order.

No other field is touched.

When nothing is produced. A proposal is generated only for a candidate that the approval gate would accept
(``approval.eligibility``: verified FEASIBLE, ranked, with a positive score and a recorded plan). A candidate that is
INFEASIBLE or INSUFFICIENT_EVIDENCE gets no text at all, not a draft and not a hint, and says why. A Deployment that
cannot be named from the evidence, or one that already has a constraint on the same topology key (a strategic merge
would silently rewrite it), is refused rather than guessed.

What the text does not claim. Adding a spread constraint edits the pod template, so the Deployment rolls out; it
does not move a running pod. The pods the model predicts will land on a different node (placement churn) are listed
as a separate, NOT included replacement step, and the scheduler, not this patch, decides where replacements go. The
prediction can differ from what the scheduler does.

``applicable`` is true only when the evidence came from the connected cluster (``LIVE_EVIDENCE_SOURCE``). The same
code runs over offline artifacts for research and tests; their text is generated but marked not applicable.

Nothing here applies, writes or reads a cluster. Applying is a separate, developer-approved step.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Any

import yaml

from app.engine.recommendation_evaluation_validation.approval import LIVE_EVIDENCE_SOURCE, LiveRecommendation
from app.engine.recommendation_evaluation_validation.approval.eligibility import evaluate_eligibility
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import (
    DEFAULT_MAX_SKEW,
    STRATEGY_MODEL,
)
from app.engine.recommendation_evaluation_validation.strategy_feasibility import (
    ADDED_MARKER,
    ANTI_AFFINITY_MODEL,
    REPLICA_INCREASE_MODEL,
    has_hostname_spread,
    has_required_anti_affinity,
    self_anti_affinity_term,
)
from app.shared.schemas.deployment import DeploymentGraph, ServicePlacement

HOSTNAME = "kubernetes.io/hostname"
TOPOLOGY_SPREAD = "TOPOLOGY_SPREAD"
POD_ANTI_AFFINITY = "POD_ANTI_AFFINITY"
REPLICA_INCREASE = "REPLICA_INCREASE"
COMBINATION = "COMBINATION"
MODELS = {TOPOLOGY_SPREAD: STRATEGY_MODEL, POD_ANTI_AFFINITY: ANTI_AFFINITY_MODEL, REPLICA_INCREASE: REPLICA_INCREASE_MODEL}
GENERATED, REFUSED = "GENERATED", "REFUSED"

NOT_APPLIED = "PROPOSAL ONLY - NOT APPLIED. A prediction from a what-if simulation; nothing has been applied or measured."

DISCLOSURES = (
    "Applying this patch edits the Deployment's pod template, so the Deployment rolls out and eventually replaces "
    "every pod it owns, not only the pods listed as predicted to change node.",
    "A spread constraint only filters where newly scheduled pods may go. It does not move a running pod, and the "
    "scheduler, not this patch, chooses where replacements land; the predicted placement is a model's answer.",
    "The rollout's own transient (old pods still counted for skew and capacity while new ones schedule) is not modelled.",
)

REPLICA_DISCLOSURES = (
    "The change adds replicas, which request more CPU and memory on the nodes they land on.",
    "If a HorizontalPodAutoscaler or another controller owns the replica count, it may revert this change; that is not "
    "visible in the evidence.",
)

REPLACEMENT_NOTE = (
    "Not part of this patch. The patch does not move or delete any pod. These are the pods whose predicted node "
    "differs from today's; they change node only if and when the scheduler places their replacements there."
)


@dataclass(frozen=True)
class DeploymentPatch:
    service: str
    deployment: str
    namespace: str
    document: dict[str, Any]


@dataclass(frozen=True)
class YamlProposal:
    recommendation_id: str
    status: str                       # GENERATED | REFUSED
    applicable: bool
    code: str | None = None           # why it was refused
    reason: str | None = None         # why it was refused, or why generated text is not applicable
    yaml: str | None = None
    sha256: str | None = None
    fingerprint: str | None = None
    evidence_source: str | None = None
    patches: tuple[DeploymentPatch, ...] = ()
    disclosures: tuple[str, ...] = ()
    pod_replacement: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendationId": self.recommendation_id, "status": self.status, "applicable": self.applicable,
            "code": self.code, "reason": self.reason, "yaml": self.yaml, "sha256": self.sha256,
            "fingerprint": self.fingerprint, "evidenceSource": self.evidence_source,
            "deployments": [{"service": p.service, "deployment": p.deployment, "namespace": p.namespace}
                            for p in self.patches],
            "disclosures": list(self.disclosures), "podReplacement": dict(self.pod_replacement),
            "strategyModels": sorted(set(MODELS.values())), "appliesNothing": True,
        }


def _refuse(recommendation_id: str, code: str, reason: str, source: str | None = None) -> YamlProposal:
    return YamlProposal(recommendation_id, REFUSED, False, code=code, reason=reason, evidence_source=source)


def _strategies_of(live: LiveRecommendation, recommendation_id: str, candidate) -> dict[str, str]:
    """service -> strategy for each change in the proposal (a combination has one per member)."""
    if candidate.type != COMBINATION:
        return {candidate.service: candidate.type}
    entry = ((live.evidence.get("combinations") or {}).get("entries") or {}).get(recommendation_id) or {}
    types = {c.id: (c.type, c.service) for c in live.report.recommendations}
    return {types[m][1]: types[m][0] for m in entry.get("members") or [] if m in types}


def _graph(live: LiveRecommendation) -> DeploymentGraph | None:
    raw = (live.details or {}).get("currentDeployment")
    if not isinstance(raw, dict):
        return None
    try:
        return DeploymentGraph.model_validate(raw)
    except ValueError:
        return None


def _spread_constraint(service: ServicePlacement) -> dict[str, Any]:
    return {"maxSkew": DEFAULT_MAX_SKEW, "topologyKey": HOSTNAME, "whenUnsatisfiable": "DoNotSchedule",
            "labelSelector": {"matchLabels": dict(sorted(service.declared_intent.selector.items()))}}


def _patch_for(service: ServicePlacement, strategy: str, added: int = 0) -> DeploymentPatch | tuple[str, str]:
    """The Deployment patch for one service, or ``(code, reason)`` when it cannot be written honestly."""
    if len(service.deployment_names) != 1:
        return ("DEPLOYMENT_UNRESOLVED",
                f"Service {service.name} is not backed by exactly one Deployment in the evidence "
                f"({', '.join(service.deployment_names) or 'none named'}), so the patch has no target to name.")
    intent = service.declared_intent
    if intent is None or not intent.selector:
        return ("DEPLOYMENT_UNRESOLVED",
                f"The pod labels of {service.name} are not in the evidence, so the change cannot select them.")
    pod_spec: dict[str, Any] = {}
    spec: dict[str, Any] = {}
    if strategy == POD_ANTI_AFFINITY:
        if has_required_anti_affinity(service):
            return ("ALREADY_CONSTRAINED",
                    f"{service.name} already has required pod anti-affinity. Kubernetes replaces that list as a whole "
                    f"when it is patched, so the existing terms would be dropped; that is a different change from the "
                    f"one that was verified.")
        pod_spec["affinity"] = {"podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [
            self_anti_affinity_term(intent.selector)]}}
    else:
        if has_hostname_spread(service) or (strategy == REPLICA_INCREASE and has_required_anti_affinity(service)):
            return ("ALREADY_CONSTRAINED",
                    f"{service.name} already has a placement constraint on {HOSTNAME}. A strategic merge on the same "
                    f"topology key would rewrite it, which is a different change from the one that was verified.")
        pod_spec["topologySpreadConstraints"] = [_spread_constraint(service)]
        if strategy == REPLICA_INCREASE:
            spec["replicas"] = service.declared_replicas + added
    document = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": service.deployment_names[0], "namespace": service.namespace},
        "spec": {**spec, "template": {"spec": pod_spec}},
    }
    return DeploymentPatch(service.name, service.deployment_names[0], service.namespace, document)


def _render(recommendation_id: str, fingerprint: str, patches: list[DeploymentPatch],
            churn: list[str], models: list[str], added: list[str]) -> str:
    header = [
        f"# {NOT_APPLIED}",
        f"# Recommendation {recommendation_id}; proposal fingerprint {fingerprint}.",
        f"# Verified model: {', '.join(models)}.",
        "# This edits the pod template: the Deployment rolls out. It does NOT move running pods; the scheduler "
        "places replacements, possibly differently from the prediction.",
        f"# Pods predicted to change node (a separate step, not included): {', '.join(churn) or 'none'}.",
    ]
    if added:
        header.append(f"# Replicas this change adds (new pods, not replacements): {len(added)}.")
    documents = [yaml.safe_dump(p.document, sort_keys=False, default_flow_style=False) for p in patches]
    return "\n".join(header) + "\n" + "---\n".join(documents)


def build_yaml_proposal(live: LiveRecommendation, recommendation_id: str) -> YamlProposal:
    """The proposal for one candidate of ``live``, or a refusal that says why. Pure: it changes nothing."""
    evidence = live.evidence if isinstance(live.evidence, dict) else {}
    source = evidence.get("source")
    candidate = next((c for c in live.report.recommendations if c.id == recommendation_id), None)
    if candidate is None:
        return _refuse(recommendation_id, "CANDIDATE_NOT_FOUND",
                       f"{recommendation_id} is not in the current evaluation.", source)

    plan = (evidence.get("feasibility") or {}).get(recommendation_id) or {}
    status = plan.get("status")
    if status == "INFEASIBLE" or (status is None and not candidate.feasible):
        return _refuse(recommendation_id, "INFEASIBLE", plan.get("reason") or "The change is not feasible.", source)
    if status == "INSUFFICIENT_EVIDENCE":
        missing = "; ".join(plan.get("missingEvidence") or []) or "scheduling facts the evidence does not hold"
        return _refuse(recommendation_id, "INSUFFICIENT_EVIDENCE",
                       f"Feasibility could not be verified, so no YAML is proposed. Missing: {missing}.", source)

    standing = evaluate_eligibility(live, recommendation_id)
    if not standing.eligible:
        return _refuse(recommendation_id, "NOT_ELIGIBLE", standing.reason or "Not eligible for approval.", source)
    if candidate.type not in (TOPOLOGY_SPREAD, POD_ANTI_AFFINITY, REPLICA_INCREASE, COMBINATION):
        return _refuse(recommendation_id, "UNSUPPORTED_TYPE",
                       f"No YAML renderer exists for {candidate.type}.", source)

    graph = _graph(live)
    placement = {s.name: s for s in graph.services} if graph is not None else {}
    strategies = _strategies_of(live, recommendation_id, candidate)
    if any(kind not in MODELS for kind in strategies.values()):
        return _refuse(recommendation_id, "UNSUPPORTED_TYPE",
                       f"No YAML renderer exists for a member of {recommendation_id}.", source)
    added_names = sorted(n for n in (plan.get("placement") or {}) if ADDED_MARKER in n)
    patches: list[DeploymentPatch] = []
    for name in sorted(strategies):
        service = placement.get(name)
        added = sum(1 for n in added_names if n.startswith(f"{name}{ADDED_MARKER}"))
        patch = _patch_for(service, strategies[name], added) if service is not None else (
            "DEPLOYMENT_UNRESOLVED", f"Service {name} is not in the topology the evaluation recorded.")
        if isinstance(patch, tuple):
            return _refuse(recommendation_id, patch[0], patch[1], source)
        patches.append(patch)
    if not patches:
        return _refuse(recommendation_id, "DEPLOYMENT_UNRESOLVED", "The candidate names no service to patch.", source)

    churn = sorted(plan.get("podsToRecreate") or [])
    models = sorted({MODELS[kind] for kind in strategies.values()})
    text = _render(recommendation_id, standing.fingerprint, patches, churn, models, added_names)
    applicable = source == LIVE_EVIDENCE_SOURCE
    return YamlProposal(
        recommendation_id, GENERATED, applicable,
        reason=None if applicable else (
            f"The evidence source is {source!r}, not read from the connected cluster, so this text is for research "
            f"and testing only."),
        yaml=text, sha256=hashlib.sha256(text.encode()).hexdigest(), fingerprint=standing.fingerprint,
        evidence_source=source, patches=tuple(patches),
        disclosures=DISCLOSURES + (REPLICA_DISCLOSURES if REPLICA_INCREASE in strategies.values() else ()),
        pod_replacement={"included": False, "pods": churn, "note": REPLACEMENT_NOTE},
    )
