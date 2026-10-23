"""Node taints, read-only: the one scheduling fact the topology graph cannot carry and no other component supplies.

Taints decide where a pod may be scheduled, so Member 4's feasibility check needs them to say a change is feasible. Component 2's
``DeploymentGraph`` has no taints field and Component 2 is not changed for this; the nodes are read here, through the same
read-only client every other read uses (``KubernetesApiSource.list_objects``), and nothing is written.

What is claimed. A node whose object carries a ``spec`` and no taints has NO taints: the API omits the field when there are none.
A node that could not be read, has no ``spec``, or has a taint this module cannot parse is left OUT of the result, so the caller
treats it as unread. An unread node is never turned into "no taints": the difference is exactly what keeps a change from being
called feasible on a guess.
"""

from typing import Any, Protocol


class _Source(Protocol):
    def list_objects(self, resource: str, path: str) -> list[dict]: ...


def read_node_taints(source: _Source) -> dict[str, list[dict[str, Any]]]:
    """``node name -> [{"key", "value", "effect"}]`` for every node that was read completely."""
    taints: dict[str, list[dict[str, Any]]] = {}
    for node in source.list_objects("nodes", "/api/v1/nodes"):
        name = (node.get("metadata") or {}).get("name")
        spec = node.get("spec")
        if not name or not isinstance(spec, dict):
            continue
        raw = spec.get("taints") or []
        parsed = [{"key": t["key"], "value": t.get("value"), "effect": t["effect"]}
                  for t in raw if isinstance(t, dict) and t.get("key") and t.get("effect")]
        if len(parsed) != len(raw):
            continue        # a taint that could not be read makes the node unread, not taint-free
        taints[name] = parsed
    return taints
