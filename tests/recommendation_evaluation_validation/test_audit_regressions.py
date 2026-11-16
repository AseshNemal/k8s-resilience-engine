"""Regression cases for the defects the 2026-11-16 algorithm audit confirmed. One case per defect."""

from collections import Counter

from app.engine.recommendation_evaluation_validation.application.analysis import placement_outcome
from app.engine.recommendation_evaluation_validation.candidate_generation import CandidateSpec
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import (
    FeasibilityStatus,
    SchedulingEvidence,
    check_topology_spread_feasibility,
)
from app.engine.recommendation_evaluation_validation.service import evaluate_candidate
from app.engine.recommendation_evaluation_validation.strategy_feasibility import (
    check_replica_increase_feasibility,
    check_replica_increase_plus_two_feasibility,
)
from tests.support.member4_harness import make_deployment, make_node, make_service, no_taints

FEASIBLE, INFEASIBLE, INSUFFICIENT = (FeasibilityStatus.FEASIBLE, FeasibilityStatus.INFEASIBLE,
                                      FeasibilityStatus.INSUFFICIENT_EVIDENCE)
UNTOLERATED = {"key": "node-role.kubernetes.io/control-plane", "value": "", "effect": "NoSchedule"}


# --- an added replica holds no capacity yet -----------------------------------------------------------------------------

def test_an_added_replica_must_fit_in_free_capacity_and_is_not_credited_the_room_of_a_running_one():
    # n1 runs the only replica and has nothing free; n2 has room for exactly one more (requests are 100m CPU). Three replicas
    # over two nodes need two on one node, and neither node can hold a second NEW one.
    g = make_deployment([make_service("svc", ["n1"])], [make_node("n1", cpu=0.0), make_node("n2", cpu=0.1)])

    assert check_replica_increase_plus_two_feasibility(g, "svc", no_taints(g)).status is INFEASIBLE
    one_more = check_replica_increase_feasibility(g, "svc", no_taints(g))      # one added replica does fit, on n2
    assert one_more.status is FEASIBLE and Counter(one_more.placement.values()) == {"n1": 1, "n2": 1}


# --- the model reads the proposed constraint the way the YAML writes it (nodeTaintsPolicy: Honor) ----------------------------

def test_a_node_with_an_untolerated_taint_is_not_a_domain_of_the_proposed_constraint():
    # Two usable workers and a tainted node, three replicas. The patch sets nodeTaintsPolicy: Honor, so the tainted node is
    # not an empty domain and 2 + 1 satisfies maxSkew 1. (Read as the default Ignore, the third replica would stay Pending.)
    g = make_deployment([make_service("svc", ["n1"] * 3)], [make_node("n1"), make_node("n2"), make_node("cp")])
    evidence = SchedulingEvidence(node_taints={"n1": [], "n2": [], "cp": [UNTOLERATED]})

    result = check_topology_spread_feasibility(g, "svc", evidence)

    assert result.status is FEASIBLE and Counter(result.placement.values()) == {"n1": 2, "n2": 1}


def test_a_full_node_whose_taints_were_not_read_leaves_the_answer_open_not_infeasible():
    # n3 cannot take a replica (no CPU). Whether it counts as an empty domain depends on its taints, which were not read:
    # counted, the third replica has nowhere to go; not counted, 2 + 1 works. That is undecided, never a proven INFEASIBLE.
    g = make_deployment([make_service("svc", ["n1"] * 3)], [make_node("n1"), make_node("n2"), make_node("n3", cpu=0.0)])
    evidence = SchedulingEvidence(node_taints={"n1": [], "n2": []})

    result = check_topology_spread_feasibility(g, "svc", evidence)

    assert result.status is INSUFFICIENT and any("node taints" in item for item in result.missing_evidence)
    assert check_topology_spread_feasibility(g, "svc", no_taints(g)).status is INFEASIBLE     # read and untainted: it counts


# --- the simulated graph describes the placement it holds ----------------------------------------------------------------------

def test_a_simulated_spread_no_longer_describes_the_service_as_concentrated():
    g = make_deployment([make_service("svc", ["n1"] * 3)], [make_node(n) for n in ("n1", "n2", "n3")])

    simulated = evaluate_candidate(g, CandidateSpec("TOPOLOGY_SPREAD", "svc", "audit"), no_taints(g)).simulated_deployment

    service = simulated.services[0]
    assert service.hosting_nodes == ["n1", "n2", "n3"] and service.concentrated is False
    assert service.replica_concentration == 1 / 3 and service.exposed_failure_domains == []
    assert g.services[0].concentrated is True and g.services[0].hosting_nodes == ["n1"]      # the original is untouched


# --- after a rollout: was the predicted distribution reached? --------------------------------------------------------------------

def _placement(*nodes):
    return {"placement": {"svc": {"pods": [{"pod": f"p{i}", "node": node, "ready": True} for i, node in enumerate(nodes)]}}}


def test_the_observed_distribution_is_compared_with_the_predicted_one():
    predicted = _placement("n1", "n2", "n3")

    reached = placement_outcome(predicted, _placement("n3", "n1", "n2"))
    differently = placement_outcome(predicted, _placement("n1", "n1", "n2"))
    not_at_all = placement_outcome(predicted, _placement("n1", "n1", "n1"))

    assert reached["matchesTarget"] is True
    assert differently["matchesTarget"] is False and differently["services"]["svc"]["readyOnTwoOrMoreNodes"] is True
    assert not_at_all["matchesTarget"] is False and not_at_all["services"]["svc"]["readyOnTwoOrMoreNodes"] is False
    assert placement_outcome(None, _placement("n1", "n2"))["matchesTarget"] is None       # no target recorded: unknown
    assert placement_outcome(predicted, {"blastRadius": 0.0}) is None                       # nothing observed


# --- a replica increase that buys nothing a cheaper change does not is said to be dominated ------------------------------------

def test_dominated_means_no_better_on_any_measure_the_model_has():
    from tests.support.member4_harness import evaluate_offline, make_workflow

    def ranking(replicas):
        live = evaluate_offline(
            make_workflow("checkout", ["payment"], []),
            make_deployment([make_service("payment", ["n1"] * replicas)], [make_node(n) for n in ("n1", "n2", "n3")]), failed_node="n1")
        return live.evidence["ranking"]["candidates"]

    # Three replicas on n1: two more replicas cost more but move FEWER pods than the spread, so it is not dominated.
    three = ranking(3)
    assert three["rec-checkout-payment-replica-increase-plus-2"]["rank"] == 2
    assert three["rec-checkout-payment-replica-increase-plus-2"]["dominatedBy"] is None
    # One replica: +2 does nothing +1 does not, moves no fewer pods and costs more: dominated. The anti-affinity form of +1 is
    # equal on every measure (it only differs in strictness, which the model does not score), so it is not.
    one = ranking(1)
    assert one["rec-checkout-payment-replica-increase-plus-2"]["dominatedBy"] == "rec-checkout-payment-replica-increase"
    assert one["rec-checkout-payment-replica-increase-anti-affinity"]["dominatedBy"] is None


# --- a service optional in one workflow and mandatory in another keeps its candidates ---------------------------------------------

def test_across_workflows_a_service_is_left_out_only_when_no_workflow_needs_it():
    from app.engine.recommendation_evaluation_validation.portfolio import WorkflowPortfolio
    from tests.support.member4_harness import PRODUCT_PAGE_VIEW, RATED_VIEW, bookinfo_scenario, bookinfo_workflow, evaluate_offline

    scenario = bookinfo_scenario("concentrated")                  # reviews and ratings both on w1
    one = scenario.evaluate()                                     # ratings is optional in productpage-view
    both = evaluate_offline(WorkflowPortfolio((bookinfo_workflow(PRODUCT_PAGE_VIEW), bookinfo_workflow(RATED_VIEW))),
                            scenario.deployment, failed_node="w1")       # ... and mandatory in rated-view

    assert one.evidence["servicesWithoutCandidates"]["services"] == ["ratings"]
    assert both.evidence["servicesWithoutCandidates"]["services"] == []
    assert any(c.service == "ratings" for c in both.report.recommendations)


# --- generated spread constraints need Kubernetes 1.27 ---------------------------------------------------------------------------

def test_only_the_api_servers_own_version_can_show_the_spread_fields_are_unsupported():
    import dataclasses

    from app.engine.recommendation_evaluation_validation.yaml_proposal import build_yaml_proposal
    from tests.support.member4_harness import bookinfo_scenario

    live = bookinfo_scenario("concentrated").evaluate()
    chosen = live.report.selected_recommendation_id

    def proposal(**version):
        return build_yaml_proposal(dataclasses.replace(
            live, evidence={**live.evidence, "schedulingFacts": {"kubernetesVersion": version}}), chosen)

    old_server = proposal(apiServer="1.25", atLeast="1.25")
    assert old_server.status == "REFUSED" and old_server.code == "KUBERNETES_VERSION_UNSUPPORTED" and old_server.yaml is None
    # an old kubelet is not an old API server: unknown, the text is still generated, and it says what to confirm
    old_kubelet = proposal(apiServer=None, atLeast="1.25")
    assert old_kubelet.status == "GENERATED" and old_kubelet.compatibility["status"] == "UNKNOWN"
    assert any("could not be confirmed" in line for line in old_kubelet.disclosures)
    assert proposal().compatibility["status"] == "UNKNOWN"
    # the API server's answer wins over an older kubelet; a new kubelet alone is a sound lower bound
    by_server = proposal(apiServer="1.31", atLeast="1.25").compatibility
    assert (by_server["status"], by_server["basis"]) == ("SUPPORTED", "api-server-version")
    lower = proposal(apiServer=None, atLeast="1.30").compatibility
    assert (lower["status"], lower["basis"]) == ("SUPPORTED", "kubelet-lower-bound")
    assert "Approval is still required" in lower["detail"]      # supported is a version check, not a guarantee


def test_the_version_is_read_from_the_api_server_and_kept_apart_from_what_the_nodes_report():
    from app.engine.recommendation_evaluation_validation.live_scheduling_evidence import kubernetes_version_fact
    from tests.support.fake_cluster import FakeClusterSource

    class Nodes(FakeClusterSource):
        def list_objects(self, resource, path):
            return [{"metadata": {"name": "w1"}, "status": {"nodeInfo": {"kubeletVersion": "v1.26.9"}}}]

    known = kubernetes_version_fact(Nodes(version="v1.31.2-gke.100"))
    unread = kubernetes_version_fact(Nodes())

    assert (known["apiServer"], known["atLeast"]) == ("1.31", "1.26")
    assert (unread["apiServer"], unread["atLeast"]) == (None, "1.26")
