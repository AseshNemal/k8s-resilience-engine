"""Constraint-aware feasibility of a topology-spread recommendation (Member 4, Phase 1).

Every graph here is built in the test: nothing reads a cluster or a sample file. A node or service starts
fully evidenced (ready, schedulable, labelled, capacity known, intent known, taints known) and each test
removes or breaks exactly the fact it is about.
"""

import pytest

from app.engine.recommendation_evaluation_validation.candidate_generation import CandidateSpec
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import (
    FeasibilityStatus,
    SchedulingEvidence,
    check_topology_spread_feasibility,
)
from app.engine.recommendation_evaluation_validation.service import evaluate_candidate
from app.shared.schemas.deployment import (
    DeclaredPlacementIntent,
    DeploymentGraph,
    NodeModel,
    PodModel,
    ServicePlacement,
    TopologySpreadConstraintModel,
)

FEASIBLE = FeasibilityStatus.FEASIBLE
INFEASIBLE = FeasibilityStatus.INFEASIBLE
INSUFFICIENT = FeasibilityStatus.INSUFFICIENT_EVIDENCE
HOSTNAME = "kubernetes.io/hostname"
ZONE = "topology.kubernetes.io/zone"


def make_node(name, *, zone="zone-a", ready=True, schedulable=True, cpu=4.0, memory=8.0, labels=...):
    if labels is ...:
        labels = {HOSTNAME: name, ZONE: zone, "disk": "ssd"}
    return NodeModel(name=name, zone=zone, cpu_available=cpu, memory_available=memory, labels=labels,
                     ready=ready, schedulable=schedulable)


def make_pods(service, placement, ready=True, owner_kind="ReplicaSet"):
    return [PodModel(name=f"{service}-{i}", node=node, ready=ready, owner_kind=owner_kind)
            for i, node in enumerate(placement)]


def make_intent(**overrides):
    fields = {"declared_replicas": 3, "selector": {"app": "db"},
              "resource_requests": [{"cpu": "500m", "memory": "512Mi"}]}
    fields.update(overrides)
    return DeclaredPlacementIntent(**fields)


def make_service(name="db", placement=("n1", "n1", "n1"), intent=..., pods=None):
    pods = pods if pods is not None else make_pods(name, placement)
    return ServicePlacement(
        name=name, declared_replicas=len(pods), ready_replicas=sum(p.ready for p in pods), pods=pods,
        declared_intent=make_intent() if intent is ... else intent,
    )


def make_graph(nodes, *services):
    return DeploymentGraph(nodes=list(nodes), services=list(services))


def three_nodes():
    return [make_node("n1"), make_node("n2"), make_node("n3")]


def no_taints(*nodes):
    return SchedulingEvidence(node_taints={n.name: [] for n in nodes})


def check(graph, evidence=None, service="db", **options):
    nodes = graph.nodes
    return check_topology_spread_feasibility(
        graph, service, evidence if evidence is not None else no_taints(*nodes), **options)


def finding_codes(result):
    return {f.constraint for f in result.findings}


# --- the happy path --------------------------------------------------------------------------------

def test_concentrated_replicas_are_feasible_to_spread_when_every_constraint_is_verified():
    result = check(make_graph(three_nodes(), make_service()))

    assert result.status is FEASIBLE and result.feasible is True
    assert result.scheduling is FEASIBLE
    assert result.single_node_resilience is True
    assert set(result.placement) == {"db-0", "db-1", "db-2"}
    assert len(set(result.placement.values())) == 3  # one replica per node under maxSkew=1
    assert result.missing_evidence == ()


# --- node readiness and schedulability -------------------------------------------------------------

@pytest.mark.parametrize("broken", [{"ready": False}, {"schedulable": False}], ids=["not-ready", "cordoned"])
def test_a_node_that_cannot_take_pods_is_never_a_placement_target(broken):
    nodes = [make_node("n1"), make_node("n2"), make_node("n3", **broken), make_node("n4")]
    graph = make_graph(nodes, make_service(placement=("n1", "n1")))

    result = check(graph)

    assert result.status is FEASIBLE
    assert "n3" not in result.placement.values()


@pytest.mark.parametrize("broken", [{"ready": False}, {"schedulable": False}], ids=["not-ready", "cordoned"])
def test_fewer_than_two_usable_nodes_is_infeasible(broken):
    nodes = [make_node("n1"), make_node("n2", **broken)]

    result = check(make_graph(nodes, make_service(placement=("n1", "n1"))))

    assert result.status is INFEASIBLE and result.feasible is False
    assert result.single_node_resilience is False
    assert "NODE_READY" in finding_codes(result) or "NODE_SCHEDULABLE" in finding_codes(result)


def test_an_unusable_node_counts_as_a_skew_domain_by_default_so_the_third_replica_is_inconclusive():
    # By default Kubernetes counts every domain matching nodeSelector/affinity, tainted or not
    # (nodeTaintsPolicy: Ignore). The NotReady node holds 0 pods, which pins the global minimum at 0, so
    # maxSkew=1 allows one replica per usable node and the third would stay Pending. With
    # nodeTaintsPolicy: Honor (Kubernetes >= 1.26) the node would not count and the spread works. The
    # cluster version is not in the evidence, so this is neither feasible nor infeasible.
    nodes = [make_node("n1"), make_node("n2"), make_node("n3", ready=False)]
    graph = make_graph(nodes, make_service(placement=("n1", "n1", "n1")))

    result = check(graph)

    assert result.status is INSUFFICIENT and result.feasible is False
    assert result.placement == {}
    assert any("nodeTaintsPolicy" in item for item in result.missing_evidence)


def test_unreported_node_status_is_insufficient_not_assumed_ready():
    # `schedulable` defaults to True in the contract, so only a reported Ready condition shows the node's
    # status was read at all.
    nodes = [make_node("n1"), make_node("n2", ready=None), make_node("n3")]

    result = check(make_graph(nodes, make_service()))

    assert result.status is INSUFFICIENT and result.feasible is False
    assert any("n2" in item and "readiness" in item for item in result.missing_evidence)


# --- capacity --------------------------------------------------------------------------------------

def test_cpu_shortage_on_every_other_node_leaves_no_alternative_placement():
    # Each replica asks 0.5 CPU; n2 and n3 have 0.2 free, so nothing can move off n1.
    nodes = [make_node("n1"), make_node("n2", cpu=0.2), make_node("n3", cpu=0.2)]

    result = check(make_graph(nodes, make_service()))

    assert result.status is INFEASIBLE
    assert result.single_node_resilience is False
    assert "NODE_CAPACITY" in finding_codes(result)


def test_memory_shortage_leaves_no_alternative_placement():
    nodes = [make_node("n1"), make_node("n2", memory=0.25), make_node("n3", memory=0.25)]

    result = check(make_graph(nodes, make_service()))

    assert result.status is INFEASIBLE
    assert "NODE_CAPACITY" in finding_codes(result)


def test_capacity_is_counted_per_replica_not_just_for_one():
    # n2 fits exactly one 0.5-CPU replica, n3 none; two replicas cannot both leave n1 but one can.
    nodes = [make_node("n1"), make_node("n2", cpu=0.5), make_node("n3", cpu=0.1)]
    graph = make_graph(nodes, make_service(placement=("n1", "n1")))

    result = check(graph)

    assert result.status is FEASIBLE
    assert sorted(result.placement.values()) == ["n1", "n2"]


def test_a_node_filled_by_the_services_own_replicas_can_keep_them():
    # n1 has no CPU free because the two replicas on it already use it; moving one to n2 (room for one)
    # must be possible, so those replicas count towards n1's capacity for this service.
    nodes = [make_node("n1", cpu=0.0), make_node("n2", cpu=0.5), make_node("n3", cpu=0.0)]
    graph = make_graph(nodes, make_service(placement=("n1", "n1")))

    result = check(graph)

    assert result.status is FEASIBLE
    assert sorted(result.placement.values()) == ["n1", "n2"]


def test_a_workload_with_no_requests_is_not_limited_by_capacity():
    nodes = [make_node("n1"), make_node("n2", cpu=0.0, memory=0.0), make_node("n3", cpu=0.0, memory=0.0)]
    graph = make_graph(nodes, make_service(intent=make_intent(resource_requests=[{}])))

    assert check(graph).status is FEASIBLE


def test_unknown_node_capacity_is_insufficient_evidence():
    nodes = [make_node("n1"), make_node("n2", cpu=None), make_node("n3")]

    result = check(make_graph(nodes, make_service()))

    assert result.status is INSUFFICIENT
    assert any("n2" in item and "capacity" in item for item in result.missing_evidence)


def test_unparseable_resource_requests_are_insufficient_evidence():
    graph = make_graph(three_nodes(), make_service(intent=make_intent(resource_requests=[{"cpu": "lots"}])))

    result = check(graph)

    assert result.status is INSUFFICIENT
    assert any("request" in item for item in result.missing_evidence)


# --- node selector and node affinity ---------------------------------------------------------------

def test_a_node_selector_that_leaves_one_eligible_node_gives_scheduling_but_no_resilience():
    nodes = [make_node("n1"), make_node("n2", labels={HOSTNAME: "n2", "disk": "hdd"}),
             make_node("n3", labels={HOSTNAME: "n3", "disk": "hdd"})]
    graph = make_graph(nodes, make_service(intent=make_intent(node_selector={"disk": "ssd"})))

    result = check(graph)

    assert result.status is INFEASIBLE
    assert result.scheduling is FEASIBLE            # every replica fits on the one eligible node...
    assert result.single_node_resilience is False   # ...which is exactly why spreading cannot help
    assert "NODE_SELECTOR" in finding_codes(result)


def test_a_node_selector_that_matches_enough_nodes_is_feasible():
    graph = make_graph(three_nodes(), make_service(intent=make_intent(node_selector={"disk": "ssd"})))

    assert check(graph).status is FEASIBLE


def test_a_node_selector_on_nodes_whose_labels_were_not_read_is_insufficient():
    nodes = [make_node("n1"), make_node("n2", labels={}), make_node("n3")]
    graph = make_graph(nodes, make_service(intent=make_intent(node_selector={"disk": "ssd"})))

    result = check(graph)

    assert result.status is INSUFFICIENT
    assert any("n2" in item and "labels" in item for item in result.missing_evidence)


def test_required_node_affinity_conflict_is_infeasible():
    rules = {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": [
        {"matchExpressions": [{"key": ZONE, "operator": "In", "values": ["zone-x"]}]}]}}
    graph = make_graph(three_nodes(), make_service(
        intent=make_intent(node_affinity_configured=True, node_affinity_rules=rules)))

    result = check(graph)

    assert result.status is INFEASIBLE
    assert "NODE_AFFINITY" in finding_codes(result)


def test_required_node_affinity_that_matches_is_feasible():
    rules = {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": [
        {"matchExpressions": [{"key": "disk", "operator": "NotIn", "values": ["hdd"]}]}]}}
    graph = make_graph(three_nodes(), make_service(
        intent=make_intent(node_affinity_configured=True, node_affinity_rules=rules)))

    assert check(graph).status is FEASIBLE


def test_preferred_node_affinity_does_not_block_scheduling():
    rules = {"preferredDuringSchedulingIgnoredDuringExecution": [
        {"weight": 1, "preference": {"matchExpressions": [{"key": ZONE, "operator": "In", "values": ["zone-x"]}]}}]}
    graph = make_graph(three_nodes(), make_service(
        intent=make_intent(node_affinity_configured=True, node_affinity_rules=rules)))

    assert check(graph).status is FEASIBLE


def test_node_affinity_flagged_but_with_no_rules_captured_is_insufficient():
    graph = make_graph(three_nodes(), make_service(intent=make_intent(node_affinity_configured=True)))

    result = check(graph)

    assert result.status is INSUFFICIENT
    assert any("node affinity" in item for item in result.missing_evidence)


# --- taints and tolerations ------------------------------------------------------------------------

DEDICATED = {"key": "dedicated", "value": "batch", "effect": "NoSchedule"}


@pytest.mark.parametrize("effect", ["NoSchedule", "NoExecute"])
def test_a_tainted_node_without_a_matching_toleration_is_excluded(effect):
    # n1 is the node the planner would pick first (fewest replicas, lowest name), so ignoring its taint
    # would show up as a replica placed there.
    nodes = three_nodes() + [make_node("n4")]
    tainted = {**DEDICATED, "effect": effect}
    evidence = SchedulingEvidence(node_taints={"n1": [tainted], "n2": [], "n3": [], "n4": []})

    result = check(make_graph(nodes, make_service(placement=("n2", "n2"))), evidence)

    assert result.status is FEASIBLE
    assert "n1" not in result.placement.values()


def test_two_nodes_tainted_and_untolerated_leave_too_few_domains():
    evidence = SchedulingEvidence(node_taints={"n1": [], "n2": [DEDICATED], "n3": [DEDICATED]})

    result = check(make_graph(three_nodes(), make_service()), evidence)

    assert result.status is INFEASIBLE
    assert "NODE_TAINT" in finding_codes(result)


@pytest.mark.parametrize("toleration", [
    {"key": "dedicated", "operator": "Equal", "value": "batch", "effect": "NoSchedule"},
    {"key": "dedicated", "operator": "Exists"},
    {"operator": "Exists"},
], ids=["equal", "exists-key", "exists-all"])
def test_a_matching_toleration_makes_the_tainted_node_usable(toleration):
    evidence = SchedulingEvidence(node_taints={"n1": [], "n2": [DEDICATED], "n3": [DEDICATED]})
    graph = make_graph(three_nodes(), make_service(intent=make_intent(tolerations=[toleration])))

    assert check(graph, evidence).status is FEASIBLE


def test_a_toleration_for_another_value_does_not_help():
    evidence = SchedulingEvidence(node_taints={"n1": [], "n2": [DEDICATED], "n3": [DEDICATED]})
    other = {"key": "dedicated", "operator": "Equal", "value": "gpu", "effect": "NoSchedule"}

    result = check(make_graph(three_nodes(), make_service(intent=make_intent(tolerations=[other]))), evidence)

    assert result.status is INFEASIBLE


def test_prefer_no_schedule_taints_do_not_block():
    soft = {"key": "dedicated", "value": "batch", "effect": "PreferNoSchedule"}
    evidence = SchedulingEvidence(node_taints={"n1": [soft], "n2": [soft], "n3": [soft]})

    assert check(make_graph(three_nodes(), make_service()), evidence).status is FEASIBLE


def test_taints_that_were_never_read_are_insufficient_evidence_not_assumed_absent():
    result = check_topology_spread_feasibility(
        make_graph(three_nodes(), make_service()), "db", SchedulingEvidence(node_taints={}))

    assert result.status is INSUFFICIENT and result.feasible is False
    assert any("taints" in item for item in result.missing_evidence)


def test_taints_default_to_unknown_when_no_evidence_object_is_given():
    result = check_topology_spread_feasibility(make_graph(three_nodes(), make_service()), "db")

    assert result.status is INSUFFICIENT
    assert any("taints" in item for item in result.missing_evidence)


# --- pod affinity and anti-affinity ----------------------------------------------------------------

def self_anti_affinity(topology_key=HOSTNAME):
    return {"requiredDuringSchedulingIgnoredDuringExecution": [
        {"labelSelector": {"matchLabels": {"app": "db"}}, "topologyKey": topology_key}]}


def test_required_self_anti_affinity_caps_each_node_at_one_replica():
    nodes = [make_node("n1"), make_node("n2")]
    graph = make_graph(nodes, make_service(intent=make_intent(
        pod_anti_affinity_configured=True, pod_anti_affinity_rules=self_anti_affinity())))

    result = check(graph)

    assert result.status is INFEASIBLE   # three replicas, at most one per node, two nodes
    assert "POD_ANTI_AFFINITY" in finding_codes(result)


def test_required_self_anti_affinity_with_enough_nodes_is_feasible():
    graph = make_graph(three_nodes(), make_service(intent=make_intent(
        pod_anti_affinity_configured=True, pod_anti_affinity_rules=self_anti_affinity())))

    assert check(graph).status is FEASIBLE


def test_anti_affinity_against_another_service_blocks_the_nodes_it_runs_on():
    other = make_service("cache", placement=("n2", "n3"), intent=make_intent(selector={"app": "cache"}))
    rules = {"requiredDuringSchedulingIgnoredDuringExecution": [
        {"labelSelector": {"matchLabels": {"app": "cache"}}, "topologyKey": HOSTNAME}]}
    graph = make_graph(three_nodes(), make_service(placement=("n1", "n1"), intent=make_intent(
        pod_anti_affinity_configured=True, pod_anti_affinity_rules=rules)), other)

    result = check(graph)

    assert result.status is INFEASIBLE   # only n1 stays usable
    assert "POD_ANTI_AFFINITY" in finding_codes(result)


def test_anti_affinity_that_may_match_a_service_with_unknown_labels_is_insufficient():
    stranger = make_service("cache", placement=("n2", "n3"), intent=None)
    rules = {"requiredDuringSchedulingIgnoredDuringExecution": [
        {"labelSelector": {"matchLabels": {"app": "cache"}}, "topologyKey": HOSTNAME}]}
    graph = make_graph(three_nodes(), make_service(intent=make_intent(
        pod_anti_affinity_configured=True, pod_anti_affinity_rules=rules)), stranger)

    result = check(graph)

    assert result.status is INSUFFICIENT
    assert any("cache" in item for item in result.missing_evidence)


def test_required_pod_affinity_to_a_service_on_one_node_leaves_one_domain():
    anchor = make_service("cache", placement=("n2",), intent=make_intent(selector={"app": "cache"}))
    rules = {"requiredDuringSchedulingIgnoredDuringExecution": [
        {"labelSelector": {"matchLabels": {"app": "cache"}}, "topologyKey": HOSTNAME}]}
    graph = make_graph(three_nodes(), make_service(intent=make_intent(
        pod_affinity_configured=True, pod_affinity_rules=rules)), anchor)

    result = check(graph)

    assert result.status is INFEASIBLE
    assert "POD_AFFINITY" in finding_codes(result)


def test_pod_anti_affinity_flagged_but_with_no_rules_captured_is_insufficient():
    graph = make_graph(three_nodes(), make_service(intent=make_intent(pod_anti_affinity_configured=True)))

    assert check(graph).status is INSUFFICIENT


# --- existing topology spread constraints ----------------------------------------------------------

def zone_constraint(max_skew=1, when="DoNotSchedule", selector=...):
    return TopologySpreadConstraintModel(
        max_skew=max_skew, topology_key=ZONE, when_unsatisfiable=when,
        label_selector={"matchLabels": {"app": "db"}} if selector is ... else selector)


def test_a_compatible_existing_zone_constraint_is_feasible():
    nodes = [make_node("n1", zone="zone-a"), make_node("n2", zone="zone-b"), make_node("n3", zone="zone-a")]
    graph = make_graph(nodes, make_service(
        placement=("n1", "n1"), intent=make_intent(topology_spread_constraints=[zone_constraint()])))

    result = check(graph)

    assert result.status is FEASIBLE
    assert {"zone-a", "zone-b"} == {next(n.zone for n in nodes if n.name == v) for v in result.placement.values()}


def test_an_existing_zone_constraint_over_a_not_ready_zone_depends_on_policy_fields_the_graph_drops():
    # With the default nodeTaintsPolicy zone-b still counts with 0 pods and zone-a may hold one replica; with
    # Honor it would not count. TopologySpreadConstraintModel does not carry the policy, so the answer is
    # inconclusive rather than a verified conflict.
    nodes = [make_node("n1", zone="zone-a"), make_node("n2", zone="zone-a"),
             make_node("n3", zone="zone-b", ready=False)]
    graph = make_graph(nodes, make_service(
        placement=("n1", "n1"), intent=make_intent(topology_spread_constraints=[zone_constraint()])))

    result = check(graph)

    assert result.status is INSUFFICIENT and result.feasible is False
    assert any("nodeTaintsPolicy" in item for item in result.missing_evidence)


def test_a_conflicting_existing_zone_constraint_that_holds_under_every_policy_is_infeasible():
    # zone-b's only node has no CPU left. That does not change whether zone-b is a domain under any
    # policy, so zone-a may hold at most one replica and the second replica cannot be placed anywhere.
    nodes = [make_node("n1", zone="zone-a"), make_node("n2", zone="zone-a"),
             make_node("n3", zone="zone-b", cpu=0.0)]
    graph = make_graph(nodes, make_service(
        placement=("n1", "n1"), intent=make_intent(topology_spread_constraints=[zone_constraint()])))

    result = check(graph)

    assert result.status is INFEASIBLE
    assert "TOPOLOGY_SPREAD" in finding_codes(result)


def test_a_soft_existing_constraint_does_not_block():
    nodes = [make_node("n1", zone="zone-a"), make_node("n2", zone="zone-a"),
             make_node("n3", zone="zone-b", ready=False)]
    graph = make_graph(nodes, make_service(placement=("n1", "n1"), intent=make_intent(
        topology_spread_constraints=[zone_constraint(when="ScheduleAnyway")])))

    assert check(graph).status is FEASIBLE


def test_an_existing_constraint_on_a_key_nodes_do_not_carry_blocks_them():
    nodes = [make_node("n1"), make_node("n2", labels={HOSTNAME: "n2", "disk": "ssd"}), make_node("n3")]
    graph = make_graph(nodes, make_service(placement=("n1", "n1"), intent=make_intent(
        topology_spread_constraints=[zone_constraint()])))

    result = check(graph)

    assert result.status is FEASIBLE
    assert "n2" not in result.placement.values()


def test_an_existing_constraint_with_an_unevaluable_selector_is_insufficient():
    constraint = zone_constraint(selector={"matchExpressions": [{"key": "app", "operator": "In", "values": ["db"]}]})
    graph = make_graph(three_nodes(), make_service(intent=make_intent(topology_spread_constraints=[constraint])))

    result = check(graph)

    assert result.status is INSUFFICIENT
    assert any("spread constraint" in item for item in result.missing_evidence)


# --- replicas and failure domains ------------------------------------------------------------------

def test_a_single_replica_can_be_scheduled_but_cannot_survive_the_loss_of_its_node():
    graph = make_graph(three_nodes(), make_service(placement=("n1",)))

    result = check(graph)

    assert result.status is INFEASIBLE and result.feasible is False
    assert result.scheduling is FEASIBLE
    assert result.single_node_resilience is False
    assert "REPLICA_COUNT" in finding_codes(result)


def test_unready_replicas_are_not_counted_as_resilience():
    pods = make_pods("db", ("n1", "n1"), ready=False) + make_pods("db-x", ("n1",))
    graph = make_graph(three_nodes(), make_service(pods=pods))

    result = check(graph)

    assert result.status is INFEASIBLE
    assert result.single_node_resilience is False


def test_a_single_worker_node_is_infeasible():
    nodes = [make_node("n1")]

    result = check(make_graph(nodes, make_service(placement=("n1", "n1"))))

    assert result.status is INFEASIBLE
    assert result.single_node_resilience is False


def test_an_unknown_service_is_insufficient_evidence():
    result = check(make_graph(three_nodes(), make_service()), service="nope")

    assert result.status is INSUFFICIENT and result.feasible is False


# --- missing or incomplete scheduling evidence -----------------------------------------------------

def test_missing_declared_intent_is_insufficient_and_says_what_is_missing():
    graph = make_graph(three_nodes(), make_service(intent=None))

    result = check(graph)

    assert result.status is INSUFFICIENT and result.feasible is False
    assert any("declared placement intent" in item for item in result.missing_evidence)
    assert result.placement == {}


def test_a_verified_blocker_outranks_missing_evidence():
    # No intent is known, but only one node is Ready: no assumption about the intent can fix that.
    nodes = [make_node("n1"), make_node("n2", ready=False)]

    result = check(make_graph(nodes, make_service(intent=None, placement=("n1", "n1"))))

    assert result.status is INFEASIBLE


def test_a_service_backed_by_several_deployments_has_no_attributable_pod_template():
    graph = make_graph(three_nodes(), make_service())
    graph.services[0].deployment_names = ["db-a", "db-b"]

    result = check(graph)

    assert result.status is INSUFFICIENT
    assert any("Deployments" in item for item in result.missing_evidence)


# --- the original graph is never changed -----------------------------------------------------------

def test_checking_feasibility_does_not_change_the_graph():
    graph = make_graph(three_nodes(), make_service())
    before = graph.model_dump()

    check(graph)

    assert graph.model_dump() == before


def test_evaluating_a_feasible_candidate_places_pods_on_the_verified_nodes_only_and_keeps_the_original():
    # The NotReady node comes first, which is where a plain round-robin over every node would start.
    nodes = [make_node("n1", ready=False), make_node("n2"), make_node("n3")]
    graph = make_graph(nodes, make_service(placement=("n2", "n2")))
    before = graph.model_dump()
    spec = CandidateSpec("TOPOLOGY_SPREAD", "db", "test")

    result = evaluate_candidate(graph, spec, no_taints(*nodes))

    assert result.feasible is True
    assert result.feasibility_status is FEASIBLE
    assert {pod.node for pod in result.simulated_deployment.services[0].pods} == {"n2", "n3"}
    assert result.concentration_after == pytest.approx(0.5)
    assert graph.model_dump() == before


def test_evaluating_a_candidate_without_enough_evidence_is_not_reported_feasible_and_not_simulated():
    graph = make_graph(three_nodes(), make_service())
    spec = CandidateSpec("TOPOLOGY_SPREAD", "db", "test")

    result = evaluate_candidate(graph, spec)  # no taint evidence

    assert result.feasible is False
    assert result.feasibility_status is INSUFFICIENT
    assert result.simulated_deployment is None
    assert result.concentration_reduction == pytest.approx(0.0)


# --- a failed greedy pass is not a proof ------------------------------------------------------------

def test_a_greedy_dead_end_does_not_make_a_spread_that_exists_infeasible():
    # The nodeSelector leaves n1 and n2. db-2 is not ready, so it stays on n2 and counts there. A
    # least-loaded pass with a name tie-break puts BOTH ready replicas on n1 (n1=1, n2=1 is a tie), which is
    # a single failure domain. Putting one on n1 and one on n2 is valid and survives any one node failing.
    nodes = [make_node("n1"), make_node("n2"), make_node("n3", labels={HOSTNAME: "n3", "disk": "hdd"})]
    pods = (make_pods("db", ("n3", "n2")) + [PodModel(name="db-x", node="n2", ready=False, owner_kind="ReplicaSet")])
    graph = make_graph(nodes, make_service(
        pods=pods, intent=make_intent(node_selector={"disk": "ssd"})))
    graph.nodes[2].labels["disk"] = "hdd"
    graph.nodes[0].labels["disk"] = graph.nodes[1].labels["disk"] = "ssd"

    result = check(graph)

    assert result.status is FEASIBLE
    assert sorted(result.placement.values()) == ["n1", "n2"]
    assert result.single_node_resilience is True


def test_when_the_search_budget_runs_out_the_answer_is_inconclusive_not_infeasible():
    graph = make_graph(three_nodes(), make_service(placement=("n1",) * 4, intent=make_intent(
        pod_anti_affinity_configured=True, pod_anti_affinity_rules=self_anti_affinity())))

    exhaustive = check(graph)                     # 4 replicas, one per node, 3 nodes: really impossible
    starved = check(graph, search_budget=2)

    assert exhaustive.status is INFEASIBLE
    assert starved.status is INSUFFICIENT and starved.feasible is False
    assert starved.placement == {}
    assert any("search" in item for item in starved.missing_evidence)
    assert not any(f.status is INFEASIBLE and f.constraint in {"POD_ANTI_AFFINITY", "TOPOLOGY_SPREAD",
                                                               "FAILURE_DOMAINS", "NO_ELIGIBLE_NODE"}
                   for f in starved.findings)


# --- the proposed change does not move running pods -------------------------------------------------

def test_a_feasible_spread_lists_the_pods_that_would_have_to_be_recreated():
    graph = make_graph(three_nodes(), make_service(placement=("n1", "n1", "n1")))

    result = check(graph)

    assert result.status is FEASIBLE
    assert result.requires_pod_recreation is True
    assert len(result.pods_to_recreate) == 2         # one replica already sits where it should end up (churn)
    assert set(result.pods_to_recreate) <= {"db-0", "db-1", "db-2"}
    assert "does not move running pods" in result.reason and "placement churn" in result.reason
    assert "rolls out every pod of the Deployment" in result.reason
    assert any("rollout" in item and "placement churn" in item for item in result.assumptions)


def test_pods_that_can_stay_where_they_are_are_not_recreated():
    # The target is one replica on each of n1, n2 and n3. db-0 is on n3 and db-1 on n1 already, so only the
    # second replica on n1 has to move; recreating more would restart pods for nothing.
    graph = make_graph(three_nodes(), make_service(placement=("n3", "n1", "n1")))

    result = check(graph)

    assert result.status is FEASIBLE
    assert result.pods_to_recreate == ("db-2",)
    assert result.placement["db-0"] == "n3" and result.placement["db-1"] == "n1"


def test_a_placement_that_already_matches_needs_no_recreation():
    graph = make_graph(three_nodes(), make_service(placement=("n1", "n2", "n3")))

    result = check(graph)

    assert result.status is FEASIBLE
    assert result.pods_to_recreate == () and result.requires_pod_recreation is False


def test_the_simulated_deployment_is_a_target_state_and_says_which_pods_must_be_recreated():
    nodes = three_nodes()
    graph = make_graph(nodes, make_service(placement=("n1", "n1", "n1")))
    result = evaluate_candidate(graph, CandidateSpec("TOPOLOGY_SPREAD", "db", "test"), no_taints(*nodes))

    assert result.feasible is True
    moved = {pod.name for pod, original in zip(result.simulated_deployment.services[0].pods,
                                               graph.services[0].pods) if pod.node != original.node}
    assert moved == set(result.pods_to_recreate) and result.requires_pod_recreation is True


# --- unmodelled hard constraints that the evidence can reveal fail closed ----------------------------

@pytest.mark.parametrize("resource", ["nvidia.com/gpu", "ephemeral-storage", "hugepages-2Mi"])
def test_a_request_for_a_resource_the_nodes_do_not_report_is_insufficient(resource):
    graph = make_graph(three_nodes(), make_service(
        intent=make_intent(resource_requests=[{"cpu": "100m", resource: "1"}])))

    result = check(graph)

    assert result.status is INSUFFICIENT and result.feasible is False
    assert any(resource in item for item in result.missing_evidence)


def test_daemonset_pods_cannot_be_spread_by_a_constraint():
    graph = make_graph(three_nodes(), make_service(pods=make_pods("db", ("n1", "n1"), owner_kind="DaemonSet")))

    result = check(graph)

    assert result.status is INFEASIBLE and result.single_node_resilience is False
    assert "WORKLOAD_KIND" in finding_codes(result)


@pytest.mark.parametrize("kind", [None, "StatefulSet", "Job"])
def test_a_workload_that_is_not_known_to_be_deployment_managed_is_insufficient(kind):
    graph = make_graph(three_nodes(), make_service(pods=make_pods("db", ("n1", "n1"), owner_kind=kind)))

    result = check(graph)

    assert result.status is INSUFFICIENT and result.feasible is False
    assert any("workload kind" in item or "owner kind" in item for item in result.missing_evidence)


def test_an_existing_spread_constraint_and_filtered_out_nodes_depend_on_nodeAffinityPolicy():
    # nodeAffinityPolicy defaults to Honor, but it can be Ignore, which would count the filtered-out node as a
    # domain with 0 pods and tighten the skew. The graph does not carry the field.
    nodes = [make_node("n1"), make_node("n2"), make_node("n3", labels={HOSTNAME: "n3", ZONE: "zone-a", "disk": "hdd"})]
    graph = make_graph(nodes, make_service(placement=("n1", "n1"), intent=make_intent(
        node_selector={"disk": "ssd"}, topology_spread_constraints=[zone_constraint()])))

    result = check(graph)

    assert result.status is INSUFFICIENT
    assert any("nodeAffinityPolicy" in item for item in result.missing_evidence)


# --- verifying the placement a service already has (used to re-check both members of a combination) ---------------

def hold(graph, evidence=None, service="db", **options):
    return check(graph, evidence, service, hold_placement=True, **options)


def test_holding_the_placement_accepts_a_valid_spread_exactly_as_it_is():
    graph = make_graph(three_nodes(), make_service(placement=("n1", "n2", "n3")))

    result = hold(graph)

    assert result.status is FEASIBLE
    assert result.placement == {"db-0": "n1", "db-1": "n2", "db-2": "n3"} and result.pods_to_recreate == ()


def test_holding_the_placement_refuses_one_that_is_not_spread_even_though_a_spread_exists():
    graph = make_graph(three_nodes(), make_service(placement=("n1", "n1", "n1")))

    assert check(graph).status is FEASIBLE            # moving the pods would work
    held = hold(graph)

    assert held.status is INFEASIBLE and held.single_node_resilience is False


def test_holding_the_placement_refuses_one_that_breaks_a_cross_service_anti_affinity():
    cache = make_service("cache", placement=("n2",), intent=make_intent(selector={"app": "cache"}))
    rules = {"requiredDuringSchedulingIgnoredDuringExecution": [
        {"labelSelector": {"matchLabels": {"app": "cache"}}, "topologyKey": HOSTNAME}]}
    graph = make_graph(three_nodes(), make_service(placement=("n1", "n2"), intent=make_intent(
        pod_anti_affinity_configured=True, pod_anti_affinity_rules=rules)), cache)

    assert hold(graph).status is INFEASIBLE           # db-1 sits next to a cache pod it must not share a node with


def test_holding_the_placement_refuses_a_pod_on_a_node_it_has_no_toleration_for():
    evidence = SchedulingEvidence(node_taints={"n1": [], "n2": [DEDICATED], "n3": []})
    graph = make_graph(three_nodes(), make_service(placement=("n1", "n2")))

    assert hold(graph, evidence).status is INFEASIBLE


def test_holding_the_placement_refuses_a_skewed_spread():
    graph = make_graph(three_nodes(), make_service(placement=("n1", "n1", "n2")))      # n3 stays empty: skew 2

    assert hold(graph).status is INFEASIBLE


def test_holding_the_placement_counts_the_pods_already_there_in_the_nodes_capacity():
    # n2 has no CPU left because the two replicas on it already use it; keeping them there must be accepted.
    nodes = [make_node("n1"), make_node("n2", cpu=0.0, memory=0.0), make_node("n3")]
    graph = make_graph(nodes, make_service(placement=("n2", "n1", "n3")))

    assert hold(graph).status is FEASIBLE


def test_holding_the_placement_keeps_unknown_evidence_unknown():
    graph = make_graph(three_nodes(), make_service(placement=("n1", "n2", "n3")))

    result = check_topology_spread_feasibility(graph, "db", SchedulingEvidence(node_taints={}), hold_placement=True)

    assert result.status is INSUFFICIENT and result.feasible is False


# --- an existing pod's own required anti-affinity also forbids an incoming pod it matches -------------------------

def refuses(app, key=HOSTNAME):
    return {"requiredDuringSchedulingIgnoredDuringExecution": [
        {"labelSelector": {"matchLabels": {"app": app}}, "topologyKey": key}]}


def cache_refusing_db(placement=("n2",)):
    return make_service("cache", placement=placement, intent=make_intent(
        selector={"app": "cache"}, pod_anti_affinity_configured=True, pod_anti_affinity_rules=refuses("db")))


def test_a_node_running_a_pod_that_refuses_the_moving_service_is_not_a_target():
    nodes = three_nodes() + [make_node("n4")]
    graph = make_graph(nodes, make_service(placement=("n1", "n1")), cache_refusing_db())

    result = check(graph)

    assert result.status is FEASIBLE and "n2" not in result.placement.values()


def test_when_every_alternative_is_refused_by_existing_pods_the_spread_is_infeasible():
    graph = make_graph(three_nodes(), make_service(placement=("n1", "n1", "n1")), cache_refusing_db(("n2",)))

    result = check(graph)

    assert result.status is INFEASIBLE and "POD_ANTI_AFFINITY" in finding_codes(result)


def test_an_existing_anti_affinity_that_cannot_be_matched_against_the_moving_pods_is_insufficient():
    # cache refuses pods labelled tier=web; the db selector says nothing about tier, so it may or may not match.
    rules = {"requiredDuringSchedulingIgnoredDuringExecution": [
        {"labelSelector": {"matchLabels": {"tier": "web"}}, "topologyKey": HOSTNAME}]}
    cache = make_service("cache", placement=("n2",), intent=make_intent(
        selector={"app": "cache"}, pod_anti_affinity_configured=True, pod_anti_affinity_rules=rules))
    graph = make_graph(three_nodes(), make_service(), cache)

    result = check(graph)

    assert result.status is INSUFFICIENT and any("anti-affinity of cache" in item for item in result.missing_evidence)


def test_an_existing_anti_affinity_aimed_at_someone_else_changes_nothing():
    graph = make_graph(three_nodes(), make_service(placement=("n1", "n1", "n1")),
                       make_service("cache", placement=("n2",), intent=make_intent(
                           selector={"app": "cache"}, pod_anti_affinity_configured=True,
                           pod_anti_affinity_rules=refuses("cache"))))

    assert check(graph).status is FEASIBLE


def test_an_existing_anti_affinity_flagged_but_not_captured_is_insufficient():
    cache = make_service("cache", placement=("n2",), intent=make_intent(
        selector={"app": "cache"}, pod_anti_affinity_configured=True))
    graph = make_graph(three_nodes(), make_service(), cache)

    assert check(graph).status is INSUFFICIENT
