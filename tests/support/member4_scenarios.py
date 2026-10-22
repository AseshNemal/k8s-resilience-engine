"""The controlled scenario catalogue for Member 4: one deterministic, hand-written case per situation the research covers.

TEST-ONLY and offline (see ``member4_harness``). Each ``Case`` carries the situation it stands for, the service whose
change is judged, and what the whole chain (feasibility, ranking, YAML proposal) is expected to say, so the same table
drives both the automated matrix test and the research runner in ``experiments/member4_offline_benchmark``.

These are models of situations, not measurements of any cluster. A result obtained here says the engine behaves as
designed on that input; it is not evidence about a real deployment.
"""

from dataclasses import dataclass

from app.engine.recommendation_evaluation_validation.feasibility_analyzer import SchedulingEvidence
from tests.support.member4_harness import (
    HOSTNAME,
    Scenario,
    bookinfo_scenario,
    bookinfo_workflow,
    make_deployment,
    make_node,
    make_service,
    no_taints,
)

DEDICATED = {"key": "dedicated", "value": "reviews", "effect": "NoSchedule"}
TOLERATION = {"key": "dedicated", "operator": "Equal", "value": "reviews", "effect": "NoSchedule"}
SOFT_ANTI_AFFINITY = {"preferredDuringSchedulingIgnoredDuringExecution": [
    {"weight": 100, "podAffinityTerm": {"labelSelector": {"matchLabels": {"app": "reviews"}}, "topologyKey": HOSTNAME}}]}


@dataclass(frozen=True)
class Case:
    name: str
    covers: str                       # which situation of the research brief this is
    scenario: Scenario
    service: str                      # the service whose change is judged
    feasibility: str | None           # FEASIBLE | INFEASIBLE | INSUFFICIENT_EVIDENCE | None when no candidate exists
    yaml: str                         # GENERATED, or the refusal code
    selected: bool                    # is this service's change the engine's selection?
    documents: int = 1                # patches in the YAML when generated
    strategy: str = "TOPOLOGY_SPREAD"  # which strategy's candidate for the service is judged


def _nodes(*, w2=None, w3=None, w1=None) -> list:
    return [make_node("w1", **(w1 or {})), make_node("w2", **(w2 or {})), make_node("w3", **(w3 or {}))]


def _bookinfo(reviews_on, *, nodes=None, reviews=None, evidence=None, name: str, **service_options) -> Scenario:
    nodes = nodes or _nodes()
    services = [make_service("productpage", ["w2", "w3"]), make_service("details", ["w2", "w3"]),
                make_service("reviews", reviews_on, **service_options), make_service("ratings", ["w2", "w3"])]
    deployment = make_deployment(services, nodes)
    return Scenario(name, "", bookinfo_workflow(), deployment,
                    no_taints(deployment) if evidence is None else evidence, failed_node="w1")


def _taints(node_taints: dict) -> SchedulingEvidence:
    return SchedulingEvidence(node_taints={n: node_taints.get(n, []) for n in ("w1", "w2", "w3")})


def catalogue() -> list[Case]:
    three = ["w1", "w1", "w1"]
    small = {"requests": [{"cpu": "500m", "memory": "512Mi"}]}
    pair_services = [make_service("productpage", ["w1", "w2", "w3"]), make_service("details", ["w1", "w1"]),
                     make_service("reviews", three), make_service("ratings", ["w2", "w3"])]
    pair_deployment = make_deployment(pair_services, _nodes())
    # Two required services, each with a single replica, both on the failing node: only the pair of replica increases helps.
    singles = make_deployment([make_service("productpage", ["w1"]), make_service("details", ["w1"]),
                               make_service("reviews", ["w2", "w3"]), make_service("ratings", ["w2", "w3"])], _nodes())
    # A spread for details (two replicas) together with a replica increase for reviews (one replica), all on w1.
    mixed = make_deployment([make_service("productpage", ["w2", "w3"]), make_service("details", ["w1", "w1"]),
                             make_service("reviews", ["w1"]), make_service("ratings", ["w2", "w3"])], _nodes())

    return [
        Case("concentrated-replicas", "concentrated replicas, feasible with a measurable improvement",
             bookinfo_scenario("concentrated"), "reviews", "FEASIBLE", "GENERATED", True),
        Case("correctly-distributed", "correctly distributed replicas",
             bookinfo_scenario("distributed"), "reviews", None, "CANDIDATE_NOT_FOUND", False),
        Case("single-replica", "a single-replica service: a spread cannot help",
             _bookinfo(["w1"], name="single-replica"), "reviews", "INFEASIBLE", "INFEASIBLE", False),
        Case("single-replica-increase", "a single-replica service: one more replica, kept apart, is the only fix",
             _bookinfo(["w1"], name="single-replica-increase"), "reviews", "FEASIBLE", "GENERATED", True,
             strategy="REPLICA_INCREASE"),
        Case("insufficient-cpu", "insufficient CPU on the other nodes",
             _bookinfo(three, nodes=_nodes(w2={"cpu": 0.2}, w3={"cpu": 0.2}), name="insufficient-cpu", **small),
             "reviews", "INFEASIBLE", "INFEASIBLE", False),
        Case("insufficient-memory", "insufficient memory on the other nodes",
             _bookinfo(three, nodes=_nodes(w2={"memory": 0.25}, w3={"memory": 0.25}), name="insufficient-memory", **small),
             "reviews", "INFEASIBLE", "INFEASIBLE", False),
        Case("unschedulable-nodes", "unschedulable nodes (one cordoned, one NotReady)",
             _bookinfo(three, nodes=_nodes(w2={"schedulable": False}, w3={"ready": False}), name="unschedulable-nodes"),
             "reviews", "INFEASIBLE", "INFEASIBLE", False),     # both blockers are observed, so it is verified, not unknown
        Case("taints-block", "taints without a toleration on every other node",
             _bookinfo(three, evidence=_taints({"w2": [DEDICATED], "w3": [DEDICATED]}), name="taints-block"),
             "reviews", "INFEASIBLE", "INFEASIBLE", False),
        Case("taints-tolerated", "taints the workload tolerates",
             _bookinfo(three, evidence=_taints({"w2": [DEDICATED], "w3": [DEDICATED]}), name="taints-tolerated",
                       tolerations=[TOLERATION]), "reviews", "FEASIBLE", "GENERATED", True),
        Case("taints-unread", "taints that were never read",
             _bookinfo(three, evidence=SchedulingEvidence(), name="taints-unread"),
             "reviews", "INSUFFICIENT_EVIDENCE", "INSUFFICIENT_EVIDENCE", False),
        Case("conflicting-constraints", "conflicting scheduling constraints (node selector leaves one node)",
             _bookinfo(three, nodes=_nodes(w2={"labels": {HOSTNAME: "w2"}}, w3={"labels": {HOSTNAME: "w3"}},
                                           w1={"labels": {HOSTNAME: "w1", "tier": "db"}}),
                       name="conflicting-constraints", node_selector={"tier": "db"}),
             "reviews", "INFEASIBLE", "INFEASIBLE", False),
        Case("multi-service-mandatory", "a workflow with several mandatory services that fail together",
             Scenario("multi-service-mandatory", "", bookinfo_workflow(), pair_deployment, no_taints(pair_deployment), "w1"),
             "reviews", "FEASIBLE", "GENERATED", True, documents=2, strategy="COMBINATION"),
        Case("no-feasible-recommendation", "no feasible recommendation exists (a single replica that cannot grow)",
             _bookinfo(["w1"], nodes=_nodes(w2={"cpu": 0.2}, w3={"cpu": 0.2}), name="no-feasible-recommendation", **small),
             "reviews", "INFEASIBLE", "INFEASIBLE", False, strategy="REPLICA_INCREASE"),
        Case("soft-anti-affinity-hardened", "soft anti-affinity made required (offered beside the spread, which wins the tie)",
             _bookinfo(three, name="soft-anti-affinity-hardened", pod_anti_affinity_rules=SOFT_ANTI_AFFINITY),
             "reviews", "FEASIBLE", "GENERATED", False, strategy="POD_ANTI_AFFINITY"),
        Case("anti-affinity-too-strict", "required anti-affinity with more replicas than nodes leaves replicas Pending",
             _bookinfo(["w1"] * 4, name="anti-affinity-too-strict", pod_anti_affinity_rules=SOFT_ANTI_AFFINITY),
             "reviews", "INFEASIBLE", "INFEASIBLE", False, strategy="POD_ANTI_AFFINITY"),
        Case("pair-of-replica-increases", "several mandatory single-replica services failing together",
             Scenario("pair-of-replica-increases", "", bookinfo_workflow(), singles, no_taints(singles), "w1"),
             "productpage", "FEASIBLE", "GENERATED", True, documents=2, strategy="COMBINATION"),
        Case("spread-and-replica-increase", "a pair mixing a spread and a replica increase",
             Scenario("spread-and-replica-increase", "", bookinfo_workflow(), mixed, no_taints(mixed), "w1"),
             "details", "FEASIBLE", "GENERATED", True, documents=2, strategy="COMBINATION"),
    ]
