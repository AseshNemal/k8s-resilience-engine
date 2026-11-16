# Handoff to Member 3: a Pending pod breaks portfolio prediction (CrossLayerGraphError)

From Member 4. A report, not a change: nothing in Member 3's files has been touched. All of this was reproduced
against the current code with the fake cluster used by the unit tests.

## What happens

`build_cross_layer_graph` (`failure_impact_prediction/cross_layer_graph.py`) raises
`CrossLayerGraphError: Pods reference unknown deployment nodes: unscheduled` when ANY pod of ANY service in the
deployment sits on node `"unscheduled"`. The check runs over every service, not only the ones the workflow
uses. `CrossLayerGraphError` is a `ValueError`, so nothing catches it inside the module.

## Where it is reachable, and where it is not

| Path | Result |
|---|---|
| single workflow: `get_runtime_component_inputs` -> `discover_live` -> `predict_failure_impact` | **Not affected.** Real discovery reports a Pending pod as unresolved (`POD_NOT_SCHEDULED`), leaves it out of the graph and marks the service's evidence incomplete (`UNSCHEDULED_PODS`). |
| portfolio: `get_runtime_portfolio_inputs` -> `_load_deployment` -> `get_live_deployment_graph` (per-namespace builder) -> `predict_portfolio_failure_impact` | **Affected.** The builder gives a node-less pod the node name `"unscheduled"` and keeps it in the graph. |

Reproduction (namespace `shop`, workflow `checkout` = web -> db, nodes w1 and w2, db and web spread over both,
plus an unrelated service `batch` whose only pod is Pending with no node):

```python
inputs = get_runtime_portfolio_inputs(["checkout"], "shop")
# inputs.deployment has batch-1 on node "unscheduled"
predict_portfolio_failure_impact(list(inputs.workflows), inputs.deployment, "w1")
# -> CrossLayerGraphError: Pods reference unknown deployment nodes: unscheduled
```

Through the route `GET /api/failure-impacts/portfolio/runtime` this surfaces as HTTP 422 with that internal
message. So one Pending pod anywhere in a namespace, even in a service no workflow uses, refuses portfolio
prediction for every workflow in it. The two Member 2 paths also disagree about Pending pods: discovery omits
them (and says so), the per-namespace builder keeps them under a sentinel node.

## Why Member 4 cares

Member 4's optional portfolio ranking would take its graded blast radius from `predict_portfolio_failure_impact`,
so it inherits this refusal. The single-workflow ranking is not affected.

## Options (Member 3's call; Member 4 needs none of them for Phase 2)

1. Treat a pod on `"unscheduled"` as an unplaced, not-ready pod: skip it in the unknown-node check and in
   placement, the way discovery already omits it.
2. Only validate the pods of services the workflow references, as the evidence check already does.
3. At minimum, raise a coded `EvidenceError` (for example `TOPOLOGY_INCOMPLETE`, with the Pending pods named)
   instead of a bare `ValueError`, so the route answers consistently with the single-workflow path.
