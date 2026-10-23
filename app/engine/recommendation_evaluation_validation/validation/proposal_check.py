"""Check the YAML a run is about to write against the Deployments that are in the cluster right now.

The approval already binds the YAML to a verified plan (``ValidationPlan.check_current``). That check answers "is this
still the proposal the server would generate?". This one answers a different question, at the last moment before a write:
"is this text something the experiment is allowed to do to THIS cluster?". It is deliberately narrower than the
generator, so a bug in the generator, or text that was edited on its way here, cannot widen what gets written.

What may be written: a strategic-merge patch of an existing Deployment of the experiment namespace that sets only

* ``spec.replicas``, and only upwards by at most ``max_added_replicas``;
* ``spec.template.spec.topologySpreadConstraints`` on the hostname key, ``DoNotSchedule``, selecting the Deployment's
  own pods;
* ``spec.template.spec.affinity.podAntiAffinity.requiredDuringSchedulingIgnoredDuringExecution`` on the hostname key,
  selecting the Deployment's own pods.

Nothing else, at any level: no container, image, label, node name, strategy, status or second kind of object.

Refused, not repaired. A constraint that would overwrite one already in the cluster is a different change from the one
that was verified. A Deployment whose ``resourceVersion`` is not the one captured with the baseline has been edited since
the baseline, so the baseline no longer describes what is being changed. A Deployment with no baseline version is treated
the same way, never as "probably unchanged".

Pure: it reads text and the facts a gateway already read, and writes nothing.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

from app.engine.recommendation_evaluation_validation.validation.cluster_gateway import HOSTNAME, LiveDeployment
from app.engine.recommendation_evaluation_validation.validation.models import ValidationRefusedError

DEFAULT_MAX_ADDED_REPLICAS = 5
REQUIRED = "requiredDuringSchedulingIgnoredDuringExecution"


@dataclass(frozen=True)
class ProposedPatch:
    name: str
    namespace: str
    document: dict[str, Any]


def _not_allowed(where: str, detail: str) -> ValidationRefusedError:
    return ValidationRefusedError("PROPOSAL_NOT_ALLOWED", f"{where}: {detail}")


def _only(actual: Mapping[str, Any], allowed: set[str], where: str) -> None:
    extra = sorted(set(actual) - allowed)
    if extra:
        raise _not_allowed(where, f"may not set {', '.join(extra)}")


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _not_allowed(where, "must be a mapping")
    return value


def _match_labels(selector: Any, live: LiveDeployment, where: str) -> None:
    """The selector must select this Deployment's own pods: every pair it names is a label the pods carry."""
    labels = _mapping(selector, f"{where}.labelSelector")
    _only(labels, {"matchLabels"}, f"{where}.labelSelector")
    pairs = _mapping(labels.get("matchLabels"), f"{where}.labelSelector.matchLabels")
    if not pairs or any(live.pod_labels.get(key) != value for key, value in pairs.items()):
        raise ValidationRefusedError(
            "PROPOSAL_SELECTOR_MISMATCH",
            f"{where} selects {dict(pairs)}, which is not a subset of the labels of {live.name}'s pods "
            f"({dict(live.pod_labels)}), so it would constrain other pods or none.")


def _check_spread(constraints: Any, live: LiveDeployment, where: str) -> None:
    if not isinstance(constraints, list) or not constraints:
        raise _not_allowed(where, "must be a non-empty list")
    if live.has_hostname_spread:
        raise ValidationRefusedError(
            "PROPOSAL_WOULD_OVERWRITE", f"{live.name} already has a spread constraint on {HOSTNAME}; a strategic merge "
            f"would rewrite it, which is a different change from the one that was verified.")
    for constraint in constraints:
        constraint = _mapping(constraint, where)
        _only(constraint, {"maxSkew", "topologyKey", "whenUnsatisfiable", "labelSelector"}, where)
        if constraint.get("topologyKey") != HOSTNAME:
            raise _not_allowed(where, f"may only spread over {HOSTNAME}")
        if constraint.get("whenUnsatisfiable") != "DoNotSchedule":
            raise _not_allowed(where, "must be DoNotSchedule, the behaviour that was verified")
        if not isinstance(constraint.get("maxSkew"), int) or constraint["maxSkew"] < 1:
            raise _not_allowed(where, "maxSkew must be a positive integer")
        _match_labels(constraint.get("labelSelector"), live, where)


def _check_anti_affinity(affinity: Any, live: LiveDeployment, where: str) -> None:
    affinity = _mapping(affinity, where)
    _only(affinity, {"podAntiAffinity"}, where)
    anti = _mapping(affinity.get("podAntiAffinity"), f"{where}.podAntiAffinity")
    _only(anti, {REQUIRED}, f"{where}.podAntiAffinity")
    terms = anti.get(REQUIRED)
    if not isinstance(terms, list) or not terms:
        raise _not_allowed(f"{where}.podAntiAffinity", "must carry at least one required term")
    if live.has_required_anti_affinity:
        raise ValidationRefusedError(
            "PROPOSAL_WOULD_OVERWRITE", f"{live.name} already has required pod anti-affinity; that list is replaced "
            f"as a whole when patched, so the existing terms would be dropped.")
    for term in terms:
        term = _mapping(term, f"{where}.podAntiAffinity")
        _only(term, {"labelSelector", "topologyKey"}, f"{where}.podAntiAffinity")
        if term.get("topologyKey") != HOSTNAME:
            raise _not_allowed(where, f"anti-affinity may only be over {HOSTNAME}")
        _match_labels(term.get("labelSelector"), live, f"{where}.podAntiAffinity")


def _check_document(document: Any, live: Mapping[str, LiveDeployment], namespace: str,
                    max_added_replicas: int) -> ProposedPatch:
    document = _mapping(document, "document")
    _only(document, {"apiVersion", "kind", "metadata", "spec"}, "document")
    if document.get("apiVersion") != "apps/v1" or document.get("kind") != "Deployment":
        raise _not_allowed("document", f"only apps/v1 Deployments may be written, not "
                           f"{document.get('apiVersion')!r} {document.get('kind')!r}")
    metadata = _mapping(document.get("metadata"), "metadata")
    _only(metadata, {"name", "namespace"}, "metadata")
    name = metadata.get("name")
    if metadata.get("namespace") != namespace:
        raise ValidationRefusedError("PROPOSAL_WRONG_NAMESPACE", f"{name!r} is in namespace {metadata.get('namespace')!r}; "
                                     f"a run writes only to the experiment namespace {namespace!r}.")
    if name not in live:
        raise ValidationRefusedError("PROPOSAL_UNKNOWN_DEPLOYMENT", f"The cluster has no Deployment {name!r} in {namespace!r}.")
    current = live[name]

    spec = _mapping(document.get("spec"), "spec")
    _only(spec, {"replicas", "template"}, "spec")
    if "replicas" in spec:
        replicas = spec["replicas"]
        if (isinstance(replicas, bool) or not isinstance(replicas, int) or replicas <= current.replicas
                or replicas - current.replicas > max_added_replicas):
            raise ValidationRefusedError(
                "PROPOSAL_REPLICAS_INVALID", f"{name} runs {current.replicas} replicas; the patch may raise that by "
                f"1 to {max_added_replicas}, not set it to {replicas!r}.")
    template = _mapping(spec.get("template"), "spec.template")
    _only(template, {"spec"}, "spec.template")
    pod_spec = _mapping(template.get("spec"), "spec.template.spec")
    _only(pod_spec, {"topologySpreadConstraints", "affinity"}, "spec.template.spec")
    if "topologySpreadConstraints" in pod_spec:
        _check_spread(pod_spec["topologySpreadConstraints"], current, "spec.template.spec.topologySpreadConstraints")
    if "affinity" in pod_spec:
        _check_anti_affinity(pod_spec["affinity"], current, "spec.template.spec.affinity")
    if not pod_spec and "replicas" not in spec:
        raise _not_allowed("spec", "the patch changes nothing")
    return ProposedPatch(name, namespace, dict(document))


def check_proposal(yaml_text: str, live: Mapping[str, LiveDeployment], *, namespace: str,
                   baseline_versions: Mapping[str, str],
                   max_added_replicas: int = DEFAULT_MAX_ADDED_REPLICAS) -> tuple[ProposedPatch, ...]:
    """The patches ``yaml_text`` contains, or a ``ValidationRefusedError`` naming the first thing not allowed."""
    try:
        documents = [doc for doc in yaml.safe_load_all(yaml_text) if doc]
    except yaml.YAMLError as error:
        raise ValidationRefusedError("PROPOSAL_UNPARSEABLE", f"The proposal is not readable YAML: {error}") from error
    if not documents:
        raise ValidationRefusedError("PROPOSAL_EMPTY", "The proposal contains no document to write.")

    patches = [_check_document(doc, live, namespace, max_added_replicas) for doc in documents]
    names = [patch.name for patch in patches]
    if len(set(names)) != len(names):
        raise _not_allowed("proposal", "names the same Deployment more than once")
    for patch in patches:
        recorded = baseline_versions.get(patch.name)
        if recorded is None or recorded != live[patch.name].resource_version:
            raise ValidationRefusedError(
                "DEPLOYMENT_DRIFTED", f"{patch.name} is at version {live[patch.name].resource_version!r}, not the "
                f"{recorded!r} captured with the baseline, so the baseline no longer describes what would be changed.")
    return tuple(patches)
