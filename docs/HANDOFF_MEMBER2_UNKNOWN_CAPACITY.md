# Handoff to Member 2: an unreadable node capacity becomes 0.0 in `analyze_live_topology`

From Member 4. Nothing of Member 2's was changed. This is an observation for its owner, found while taking Member 2's node facts for
Member 4's feasibility check (see `docs/MEMBER4_INPUT_SOURCES.md`).

## What was observed

Two public Member 2 paths disagree about a node whose CPU could not be read (reproduced with `tests/support/fake_cluster.py`, a node
built with `cpu=None`, namespace `shop`):

| Path | `cpu_available` of that node |
| --- | --- |
| `discovery.discover` / `service.discover_live` | `None` (unknown) |
| `service.analyze_live_topology` (`.graph.nodes`) | `0.0` |

The contract says unknown facts stay `None`. A `0.0` reads as "this node has no CPU left", which is a different claim.

## Why it matters to a consumer

Member 4's capacity check treats `None` as "capacity unknown, so the change is INSUFFICIENT_EVIDENCE" and `0.0` as "no room, so the change is
INFEASIBLE". Taking the fuller analysis at face value turned the first into the second. Member 4 now never takes capacity from
`analyze_live_topology` where the evaluated graph does not already know it (`live_scheduling_evidence.py`, and the test
`test_an_unknown_capacity_is_never_filled_in_from_the_fuller_analysis_which_reports_it_as_zero`), so nothing is wrong on Member 4's side. Anyone
else reading `analyze_live_topology` for capacity has the same exposure.

## Suggested change (Member 2's to make or decline)

Where the live-state parser subtracts pod requests from allocatable, keep `None` when allocatable could not be read instead of coercing to `0.0`.

## Also noted

`add_scheduling_evidence`'s docstring in `tests/recommendation_evaluation_validation/test_live_recommendation.py` says live discovery returns nodes with no
labels or Ready state and services with no intent. That is true of `discover_live` and false of `analyze_live_topology`, which does carry them.
