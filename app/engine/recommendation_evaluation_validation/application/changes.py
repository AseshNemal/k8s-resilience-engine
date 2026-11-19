"""What a change did to each Deployment, in words, and whether the cluster still holds what it set.

Apply and Reset only ever touch three fields of a Deployment: the replica count, the topology spread constraints and the pod
anti-affinity. Those are the fields this application OWNS on a change it applied. Everything here is about those three and
nothing else, so a description can never claim more than was written and a Reset can never restore more than was changed.

``describe`` turns two snapshots (before and after) into rows a person can read without opening the YAML.
``drift`` compares what a change set with what the cluster holds now, field by field. A field that differs was changed by
something else since (a person, a controller, a later change), and Reset leaves it alone unless it is told otherwise.
"""

from copy import deepcopy
from collections.abc import Mapping, Sequence
from typing import Any

HOSTNAME = "kubernetes.io/hostname"
REQUIRED = "requiredDuringSchedulingIgnoredDuringExecution"
PREFERRED = "preferredDuringSchedulingIgnoredDuringExecution"

REPLICAS, SPREAD, ANTI_AFFINITY = "replicas", "topology_spread_constraints", "pod_anti_affinity"
OWNED_FIELDS = (REPLICAS, SPREAD, ANTI_AFFINITY)
LABELS = {REPLICAS: "Replicas", SPREAD: "Topology spread", ANTI_AFFINITY: "Pod anti-affinity"}


def _value(snapshot: Any, field: str) -> Any:
    """One owned field of a stored snapshot (a dict) or of a Deployment just read (an object), in one comparable shape."""
    raw = snapshot.get(field) if isinstance(snapshot, Mapping) else getattr(snapshot, field, None)
    if field == REPLICAS:
        return raw
    if field == SPREAD:
        return sorted([dict(constraint) for constraint in (raw or ())], key=lambda c: c.get("topologyKey", ""))
    return dict(raw) if raw else None      # an empty anti-affinity and an absent one are the same thing


def owned(snapshot: Any) -> dict[str, Any]:
    return {field: _value(snapshot, field) for field in OWNED_FIELDS}


def proposed(before: Any, document: Mapping[str, Any]) -> dict[str, Any]:
    """Expected owned values of an already-whitelisted strategic merge patch, not observed evidence."""
    result = deepcopy(owned(before))
    spec = document["spec"]
    pod = spec.get("template", {}).get("spec", {})
    if "replicas" in spec:
        result[REPLICAS] = spec["replicas"]
    if "topologySpreadConstraints" in pod:
        merged = {c["topologyKey"]: c for c in result[SPREAD]}
        merged.update({c["topologyKey"]: c for c in pod["topologySpreadConstraints"]})
        result[SPREAD] = sorted(merged.values(), key=lambda c: c["topologyKey"])
    if "affinity" in pod:
        result[ANTI_AFFINITY] = {**(result[ANTI_AFFINITY] or {}), **pod["affinity"]["podAntiAffinity"]}
    return result


def _domain(key: str | None) -> str:
    return "nodes" if key == HOSTNAME else f"the {key} domains" if key else "an unnamed topology"


def _spread_text(constraints: Sequence[Mapping[str, Any]]) -> str:
    if not constraints:
        return "none"
    parts = []
    for c in constraints:
        hard = c.get("whenUnsatisfiable") == "DoNotSchedule"
        parts.append(f"{'must' if hard else 'should'} spread across {_domain(c.get('topologyKey'))} "
                     f"(at most {c.get('maxSkew', '?')} apart)")
    return "; ".join(parts)


def _anti_text(anti: Mapping[str, Any] | None) -> str:
    if not anti:
        return "none"
    parts = []
    for key, word in ((REQUIRED, "required"), (PREFERRED, "preferred")):
        terms = anti.get(key) or []
        if terms:
            keys = sorted({(t.get("podAffinityTerm") or t).get("topologyKey") for t in terms} - {None})
            parts.append(f"{word}: no two pods on the same {' / '.join('node' if k == HOSTNAME else k for k in keys) or 'domain'}")
    return "; ".join(parts) or "none"


def text_of(field: str, value: Any) -> str:
    if field == REPLICAS:
        return "unknown" if value is None else str(value)
    return _spread_text(value or []) if field == SPREAD else _anti_text(value)


def describe(before: Sequence[Any], after: Sequence[Any] | None) -> list[dict[str, Any]]:
    """Per Deployment: every owned field, its value before and after, and whether the change altered it.

    ``after`` None means what the change set was never recorded; the rows then carry ``to: None`` and ``changed: None``
    (unknown), never a guess.
    """
    later = {(_name(a)): a for a in (after or [])}
    rows = []
    for snapshot in before:
        name = _name(snapshot)
        now = later.get(name)
        fields = []
        for field in OWNED_FIELDS:
            old = _value(snapshot, field)
            new = _value(now, field) if now is not None else None
            known = now is not None
            fields.append({
                "field": field, "label": LABELS[field], "from": old, "to": new if known else None,
                "fromText": text_of(field, old), "toText": text_of(field, new) if known else None,
                "changed": (old != new) if known else None})
        rows.append({"deployment": name, "namespace": _namespace(snapshot), "uid": _uid(snapshot), "fields": fields,
                     "summary": summarise(name, fields)})
    return rows


def summarise(name: str, fields: Sequence[Mapping[str, Any]]) -> str:
    changed = [f for f in fields if f["changed"]]
    if any(f["changed"] is None for f in fields):
        return f"{name}: the values now on the cluster could not be read"
    if not changed:
        return f"{name}: no owned field changed"
    return f"{name}: " + "; ".join(f"{f['label'].lower()} {f['fromText']} → {f['toText']}" for f in changed)


def drift(applied: Any, live: Any) -> list[dict[str, Any]]:
    """The owned fields whose value in the cluster is no longer what this change set: changed by something else since."""
    found = []
    for field in OWNED_FIELDS:
        was_set, now = _value(applied, field), _value(live, field)
        if was_set != now:
            found.append({"field": field, "label": LABELS[field], "applied": was_set, "live": now,
                          "appliedText": text_of(field, was_set), "liveText": text_of(field, now)})
    return found


def _name(snapshot: Any) -> str:
    return snapshot["name"] if isinstance(snapshot, Mapping) else snapshot.name


def _namespace(snapshot: Any) -> str | None:
    return snapshot.get("namespace") if isinstance(snapshot, Mapping) else getattr(snapshot, "namespace", None)


def _uid(snapshot: Any) -> str | None:
    return snapshot.get("uid") if isinstance(snapshot, Mapping) else getattr(snapshot, "uid", None)
