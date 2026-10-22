"""The capacity ledger (Member 4, Phase 2B): CPU and memory reservations as pods move in a virtual graph.

It is tested on its own, with hand-built graphs, before anything uses it for combinations. Units are the ones
the DeploymentGraph uses: CPU in cores, memory in GiB.
"""

import random

import pytest

from app.engine.recommendation_evaluation_validation.capacity_ledger import (
    AlreadyMoved,
    CapacityExceeded,
    CapacityLedger,
    LedgerPlacementMutation,
    UnknownCapacity,
    UnknownPod,
)
from app.engine.simulation import simulate_deployment_change
from app.shared.schemas.deployment import DeclaredPlacementIntent, DeploymentGraph, NodeModel, PodModel, ServicePlacement


def node(name, cpu=4.0, memory=8.0, zone="z"):
    return NodeModel(name=name, zone=zone, cpu_available=cpu, memory_available=memory, ready=True,
                     labels={"kubernetes.io/hostname": name})


def service(name, placement, *, cpu="500m", memory="1Gi", intent=True, requests=None):
    pods = [PodModel(name=f"{name}-{i}", node=n, ready=True, owner_kind="ReplicaSet") for i, n in enumerate(placement)]
    declared = None
    if intent:
        declared = DeclaredPlacementIntent(
            selector={"app": name}, resource_requests=requests if requests is not None else [{"cpu": cpu, "memory": memory}])
    return ServicePlacement(name=name, declared_replicas=len(pods), ready_replicas=len(pods), pods=pods,
                            declared_intent=declared)


def graph(nodes, *services):
    return DeploymentGraph(nodes=list(nodes), services=list(services))


def total(ledger, names):
    cpu = [ledger.available(n)[0] for n in names]
    memory = [ledger.available(n)[1] for n in names]
    return sum(cpu), sum(memory)


# --- accounting -----------------------------------------------------------------------------------------

def test_moving_a_pod_reserves_its_request_on_the_destination_and_frees_it_on_the_origin():
    ledger = CapacityLedger(graph([node("n1", cpu=1.0, memory=2.0), node("n2", cpu=4.0, memory=8.0)],
                                  service("db", ["n1"], cpu="500m", memory="1Gi")))

    ledger.move("db-0", "n2")

    assert ledger.available("n1") == pytest.approx((1.5, 3.0))
    assert ledger.available("n2") == pytest.approx((3.5, 7.0))
    assert ledger.net_reserved("n2") == pytest.approx((0.5, 1.0)) and ledger.net_reserved("n1") == pytest.approx((-0.5, -1.0))


def test_capacity_is_conserved_across_any_sequence_of_legal_moves():
    rng = random.Random(5)
    for _ in range(200):
        names = [f"n{i}" for i in range(rng.randint(2, 5))]
        nodes = [node(n, cpu=rng.choice([1.0, 2.0, 4.0]), memory=rng.choice([2.0, 4.0, 8.0])) for n in names]
        services = [service(f"s{j}", [rng.choice(names) for _ in range(rng.randint(1, 3))],
                            cpu=rng.choice(["250m", "500m", "1"]), memory=rng.choice(["256Mi", "1Gi"]))
                    for j in range(rng.randint(1, 3))]
        g = graph(nodes, *services)
        ledger = CapacityLedger(g)
        before = total(ledger, names)
        pods = [p.name for s in services for p in s.pods]
        rng.shuffle(pods)

        for pod in pods[: rng.randint(0, len(pods))]:
            try:
                ledger.move(pod, rng.choice(names))
            except CapacityExceeded:
                pass

        assert total(ledger, names) == pytest.approx(before)          # nothing created, nothing destroyed
        assert all(c >= -1e-9 and m >= -1e-9 for c, m in (ledger.available(n) for n in names))


def test_a_move_that_does_not_fit_is_refused_and_leaves_the_ledger_unchanged():
    ledger = CapacityLedger(graph([node("n1"), node("n2", cpu=0.4, memory=8.0)], service("db", ["n1"], cpu="500m")))
    snapshot = {n: ledger.available(n) for n in ("n1", "n2")}

    with pytest.raises(CapacityExceeded) as raised:
        ledger.move("db-0", "n2")

    assert raised.value.node == "n2" and raised.value.resource == "cpu"
    assert {n: ledger.available(n) for n in ("n1", "n2")} == snapshot and ledger.moved_pods == {}


def test_floating_point_noise_does_not_refuse_a_pod_that_exactly_fits():
    # 0.7 - 0.4 is 0.29999999999999993 in floating point, but the room left really is 0.3 cores.
    ledger = CapacityLedger(graph([node("n1"), node("n2", cpu=0.7)],
                                  service("a", ["n1"], cpu="400m"), service("b", ["n1"], cpu="300m")))

    ledger.move("a-0", "n2")
    ledger.move("b-0", "n2")

    assert ledger.available("n2")[0] == pytest.approx(0.0, abs=1e-9)


def test_each_resource_is_enforced_separately():
    memory_bound = CapacityLedger(graph([node("n1"), node("n2", cpu=4.0, memory=0.5)], service("db", ["n1"], memory="1Gi")))
    cpu_bound = CapacityLedger(graph([node("n1"), node("n2", cpu=0.1, memory=8.0)], service("db", ["n1"], cpu="500m")))

    with pytest.raises(CapacityExceeded) as memory:
        memory_bound.move("db-0", "n2")
    with pytest.raises(CapacityExceeded) as cpu:
        cpu_bound.move("db-0", "n2")

    assert (memory.value.resource, cpu.value.resource) == ("memory", "cpu")


def test_capacity_reserved_by_one_change_is_not_available_to_the_next():
    # n3 has room for exactly one 500m pod. The first change takes it; the second must not reuse it.
    ledger = CapacityLedger(graph([node("n1"), node("n2"), node("n3", cpu=0.6)],
                                  service("a", ["n1"], cpu="500m"), service("b", ["n2"], cpu="500m")))

    ledger.move("a-0", "n3")

    with pytest.raises(CapacityExceeded):
        ledger.move("b-0", "n3")


def test_capacity_freed_by_a_departing_pod_can_be_reused_exactly_once():
    # n3 is full. a-0 leaves it, b-0 takes the freed room, and c-0 cannot take the same room again.
    ledger = CapacityLedger(graph([node("n1"), node("n2"), node("n3", cpu=0.0, memory=0.0)],
                                  service("a", ["n3"]), service("b", ["n1"]), service("c", ["n2"])))
    ledger.move("a-0", "n1")
    assert ledger.available("n3") == pytest.approx((0.5, 1.0))

    ledger.move("b-0", "n3")
    assert ledger.available("n3") == pytest.approx((0.0, 0.0))

    with pytest.raises(CapacityExceeded):
        ledger.move("c-0", "n3")


def test_a_pod_cannot_be_moved_twice_so_its_capacity_is_never_freed_or_reserved_twice():
    ledger = CapacityLedger(graph([node("n1"), node("n2"), node("n3")], service("db", ["n1"])))
    ledger.move("db-0", "n2")
    after_first = {n: ledger.available(n) for n in ("n1", "n2", "n3")}

    with pytest.raises(AlreadyMoved):
        ledger.move("db-0", "n3")
    with pytest.raises(AlreadyMoved):
        ledger.move("db-0", "n2")

    assert {n: ledger.available(n) for n in ("n1", "n2", "n3")} == after_first


def test_moving_a_pod_to_the_node_it_is_already_on_changes_nothing():
    ledger = CapacityLedger(graph([node("n1")], service("db", ["n1"])))

    ledger.move("db-0", "n1")

    assert ledger.available("n1") == pytest.approx((4.0, 8.0)) and ledger.moved_pods == {}


def test_two_full_nodes_cannot_swap_pods_because_each_move_must_fit_when_it_is_made():
    # A simultaneous swap would fit in the final state, but the ledger replays moves one at a time and refuses
    # to assume they happen together. That is deliberate: it never claims a placement it cannot account for.
    ledger = CapacityLedger(graph([node("n1", cpu=0.0, memory=0.0), node("n2", cpu=0.0, memory=0.0)],
                                  service("a", ["n1"]), service("b", ["n2"])))

    with pytest.raises(CapacityExceeded):
        ledger.move("a-0", "n2")
    with pytest.raises(CapacityExceeded):
        ledger.move("b-0", "n1")


def test_the_ledger_describes_the_final_state_only_and_says_so():
    assert "final state" in (CapacityLedger.__doc__ or "").lower()
    assert "rollout" in (CapacityLedger.__doc__ or "").lower()


# --- unknown evidence fails closed -----------------------------------------------------------------------

def test_unknown_destination_capacity_refuses_a_move_that_needs_it():
    ledger = CapacityLedger(graph([node("n1"), node("n2", cpu=None)], service("db", ["n1"])))

    with pytest.raises(UnknownCapacity) as raised:
        ledger.move("db-0", "n2")

    assert raised.value.node == "n2" and ledger.moved_pods == {}


def test_a_pod_that_asks_for_nothing_can_go_anywhere_even_where_capacity_is_unknown():
    ledger = CapacityLedger(graph([node("n1"), node("n2", cpu=None, memory=None)],
                                  service("db", ["n1"], requests=[{}])))

    ledger.move("db-0", "n2")

    assert ledger.available("n2") == (None, None) and ledger.moved_pods == {"db-0": ("n1", "n2")}


def test_unknown_origin_capacity_stays_unknown_after_the_pod_leaves():
    ledger = CapacityLedger(graph([node("n1", cpu=None), node("n2")], service("db", ["n1"])))

    ledger.move("db-0", "n2")

    assert ledger.available("n1")[0] is None and ledger.available("n1")[1] == pytest.approx(8.0 + 1.0)


@pytest.mark.parametrize("make", [
    lambda: service("db", ["n1"], intent=False),                                       # no declared intent
    lambda: service("db", ["n1"], requests=[]),                                         # no containers captured
    lambda: service("db", ["n1"], requests=[{"cpu": "lots"}]),                          # not a quantity
])
def test_a_pod_whose_size_is_unknown_makes_both_nodes_unknown_instead_of_guessing(make):
    ledger = CapacityLedger(graph([node("n1"), node("n2"), node("n3")], make(), service("other", ["n3"])))

    ledger.move("db-0", "n2")

    assert ledger.available("n1") == (None, None) and ledger.available("n2") == (None, None)
    assert ledger.available("n3") == pytest.approx((4.0, 8.0))           # an uninvolved node is not made unknown
    with pytest.raises(UnknownCapacity):                                  # and nothing can now be reserved on them
        ledger.move("other-0", "n2")


def test_unknown_pods_and_unknown_nodes_are_refused():
    ledger = CapacityLedger(graph([node("n1")], service("db", ["n1"])))

    with pytest.raises(UnknownPod):
        ledger.move("ghost-0", "n1")
    with pytest.raises(UnknownCapacity):
        ledger.move("db-0", "nowhere")


def test_a_pod_outside_the_tracked_nodes_frees_nothing_but_still_reserves_its_destination():
    pods = [PodModel(name="db-0", node="unscheduled", ready=False)]
    g = DeploymentGraph(nodes=[node("n1")], services=[ServicePlacement(
        name="db", declared_replicas=1, ready_replicas=0, pods=pods,
        declared_intent=DeclaredPlacementIntent(selector={"app": "db"}, resource_requests=[{"cpu": "500m", "memory": "1Gi"}]))])
    ledger = CapacityLedger(g)

    ledger.move("db-0", "n1")

    assert ledger.available("n1") == pytest.approx((3.5, 7.0))


# --- writing the result back, and keeping the original graph untouched -----------------------------------------

def test_the_ledger_never_changes_the_graph_it_was_built_from():
    g = graph([node("n1"), node("n2")], service("db", ["n1"]))
    before = g.model_dump()

    ledger = CapacityLedger(g)
    ledger.move("db-0", "n2")

    assert g.model_dump() == before


def test_writing_back_puts_the_remaining_capacity_into_a_copy_and_keeps_unknowns_unknown():
    g = graph([node("n1"), node("n2", cpu=None), node("n3")], service("db", ["n1"], requests=[{}]), service("api", ["n3"]))
    ledger = CapacityLedger(g)
    ledger.move("api-0", "n1")
    working = g.model_copy(deep=True)

    ledger.write_back(working)

    by_name = {n.name: n for n in working.nodes}
    assert (by_name["n1"].cpu_available, by_name["n1"].memory_available) == pytest.approx((3.5, 7.0))
    assert (by_name["n3"].cpu_available, by_name["n3"].memory_available) == pytest.approx((4.5, 9.0))
    assert by_name["n2"].cpu_available is None
    assert g.nodes[0].cpu_available == 4.0           # the original is untouched


# --- the virtual change that uses it ---------------------------------------------------------------------------

def test_a_placement_applied_through_the_simulation_engine_moves_pods_and_capacity_together_on_a_copy():
    g = graph([node("n1", zone="a"), node("n2", zone="b"), node("n3", zone="c")], service("db", ["n1", "n1", "n1"]))
    before = g.model_dump()

    result = simulate_deployment_change(g, LedgerPlacementMutation("db", {"db-0": "n1", "db-1": "n2", "db-2": "n3"}))

    moved = {p.name: (p.node, p.zone) for p in result.after.services[0].pods}
    assert moved == {"db-0": ("n1", "a"), "db-1": ("n2", "b"), "db-2": ("n3", "c")}
    capacity = {n.name: (n.cpu_available, n.memory_available) for n in result.after.nodes}
    assert capacity["n1"] == pytest.approx((5.0, 10.0)) and capacity["n2"] == pytest.approx((3.5, 7.0))
    assert g.model_dump() == before


def test_a_placement_that_does_not_fit_is_refused_by_the_mutation():
    g = graph([node("n1"), node("n2", cpu=0.2)], service("db", ["n1", "n1"]))

    with pytest.raises(CapacityExceeded):
        simulate_deployment_change(g, LedgerPlacementMutation("db", {"db-0": "n1", "db-1": "n2"}))


def test_moves_within_one_change_are_ordered_so_that_freed_room_is_used():
    # n2 is full: db-0 can only go there after db-1 has left it.
    # db-0 sorts first but its destination only has room once db-1 has left it, so a single pass in name order
    # would fail; the change must retry.
    g = graph([node("n1"), node("n2", cpu=0.0, memory=0.0), node("n3")],
              service("db", ["n1", "n2"]))

    result = simulate_deployment_change(g, LedgerPlacementMutation("db", {"db-0": "n2", "db-1": "n3"}))

    assert {p.name: p.node for p in result.after.services[0].pods} == {"db-0": "n2", "db-1": "n3"}


# --- failed moves never corrupt the ledger, whatever came before or after them -----------------------------------

def snapshot(ledger, names):
    return {n: ledger.available(n) for n in names}, ledger.moved_pods


def test_after_any_mix_of_successful_and_refused_moves_the_ledger_equals_one_that_replayed_only_the_successes():
    from app.engine.recommendation_evaluation_validation.capacity_ledger import LedgerError

    rng = random.Random(21)
    refusals = 0
    for _ in range(300):
        names = [f"n{i}" for i in range(rng.randint(2, 5))]
        nodes = [node(n, cpu=rng.choice([None, 0.5, 1.0, 2.0]), memory=rng.choice([None, 1.0, 4.0])) for n in names]
        services = [service(f"s{j}", [rng.choice(names) for _ in range(rng.randint(1, 3))], cpu=rng.choice(["250m", "500m", "1"]),
                            memory=rng.choice(["256Mi", "1Gi"]), intent=rng.random() > 0.2) for j in range(rng.randint(1, 3))]
        g = graph(nodes, *services)
        before = g.model_dump()
        live, successes = CapacityLedger(g), []
        pods = [p.name for s in services for p in s.pods] + ["ghost-0"]

        for _ in range(rng.randint(1, 12)):
            pod, target = rng.choice(pods), rng.choice(names + ["nowhere"])
            state_before = snapshot(live, names)
            try:
                live.move(pod, target)
                successes.append((pod, target))
            except LedgerError:
                refusals += 1
                assert snapshot(live, names) == state_before          # a refused move leaves no trace at all

        replay = CapacityLedger(g)
        for pod, target in successes:
            replay.move(pod, target)
        assert snapshot(live, names) == snapshot(replay, names)
        assert g.model_dump() == before                                # and the graph it read was never touched
    assert refusals > 100                                              # the property was really exercised on failures


def test_known_capacity_is_conserved_through_failures_too():
    rng = random.Random(8)
    for _ in range(200):
        names = [f"n{i}" for i in range(rng.randint(2, 4))]
        g = graph([node(n, cpu=rng.choice([0.5, 1.0, 2.0]), memory=rng.choice([1.0, 2.0])) for n in names],
                  *[service(f"s{j}", [rng.choice(names) for _ in range(2)], cpu="500m", memory="1Gi") for j in range(3)])
        ledger = CapacityLedger(g)
        before = total(ledger, names)
        for pod in [p.name for s in g.services for p in s.pods] * 2:       # every pod is tried twice
            try:
                ledger.move(pod, rng.choice(names))
            except (CapacityExceeded, AlreadyMoved):
                pass
        assert total(ledger, names) == pytest.approx(before)


def test_a_refused_move_part_way_through_a_change_leaves_neither_the_graph_nor_the_result_partly_applied():
    # db-0 can move to n2 (room for one), db-1 then cannot (the room is gone): the whole change is refused.
    g = graph([node("n1"), node("n2", cpu=0.6), node("n3", cpu=0.0)], service("db", ["n1", "n1"]))
    before = g.model_dump()

    with pytest.raises(CapacityExceeded):
        simulate_deployment_change(g, LedgerPlacementMutation("db", {"db-0": "n2", "db-1": "n3"}))

    assert g.model_dump() == before


def test_a_failed_change_does_not_leak_into_the_next_one():
    g = graph([node("n1"), node("n2", cpu=0.6), node("n3", cpu=0.0)], service("db", ["n1", "n1"]))
    with pytest.raises(CapacityExceeded):
        simulate_deployment_change(g, LedgerPlacementMutation("db", {"db-0": "n2", "db-1": "n3"}))

    result = simulate_deployment_change(g, LedgerPlacementMutation("db", {"db-0": "n2", "db-1": "n1"}))

    capacity = {n.name: n.cpu_available for n in result.after.nodes}
    assert capacity["n2"] == pytest.approx(0.1) and capacity["n1"] == pytest.approx(4.5) and capacity["n3"] == pytest.approx(0.0)
