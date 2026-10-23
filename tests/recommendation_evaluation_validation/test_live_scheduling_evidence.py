"""The live scheduling facts: Component 2's where it has them, an independent read where nobody does, never invented."""

from types import SimpleNamespace

import pytest

from app.engine.deployment_topology_modeling.live_state.cluster_models import ClusterNode
from app.engine.recommendation_evaluation_validation.live_scheduling_evidence import (
    enrich_scheduling_facts,
    live_scheduling_evidence,
)
from app.integrations.kubernetes.client import ConnectionFailedError
from app.integrations.kubernetes.node_taints import read_node_taints
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.deployment import DeclaredPlacementIntent
from tests.support.member4_harness import make_deployment, make_node, make_service


def bare():
    """What discover_live gives: placement, but unlabelled nodes with no Ready state, and no declared intent."""
    nodes = [make_node("w1", labels={}, ready=None, cpu=4.0, memory=8.0), make_node("w2", labels={}, ready=None, cpu=4.0, memory=8.0)]
    return make_deployment([make_service("web", ["w1", "w2"], intent=False), make_service("db", ["w1"], intent=False)], nodes)


def fuller():
    """What analyze_live_topology gives: the same cluster with labels, Ready, free capacity and intents."""
    nodes = [make_node("w1", cpu=3.4, memory=7.25), make_node("w2", cpu=3.9, memory=8.0)]
    graph = make_deployment([make_service("web", ["w1", "w2"]), make_service("db", ["w1"])], nodes)
    graph.services[0].declared_intent = DeclaredPlacementIntent(declared_replicas=2, selector={"app": "web"},
                                                                resource_requests=[{"cpu": "250m", "memory": "64Mi"}])
    return graph


def analysis(graph, source="cluster-deployment-specs"):
    return lambda namespace: SimpleNamespace(graph=graph, data_source=source)


# --- from Component 2, where it has them ----------------------------------------------------------------------------------

def test_node_facts_and_declared_intent_are_taken_from_component_2_onto_a_copy():
    original = bare()

    enriched, report = enrich_scheduling_facts(original, "shop", analysis(fuller()))

    assert enriched is not original
    assert all(n.labels == {} and n.ready is None for n in original.nodes) and original.services[0].declared_intent is None
    assert [n.labels for n in enriched.nodes] == [{"kubernetes.io/hostname": "w1"}, {"kubernetes.io/hostname": "w2"}]
    assert [n.ready for n in enriched.nodes] == [True, True]
    assert enriched.services[0].declared_intent.selector == {"app": "web"}
    assert report["source"] == "cluster-deployment-specs" and report["nodesEnriched"] == ["w1", "w2"]


def test_a_service_component_2_has_no_intent_for_stays_without_one_and_is_reported():
    broken = fuller()
    broken.services[1].declared_intent = None
    enriched, report = enrich_scheduling_facts(bare(), "shop", analysis(broken))
    assert enriched.services[1].declared_intent is None and report["servicesWithoutIntent"] == ["db"]
    assert report["servicesWithIntent"] == ["web"]


def test_free_capacity_takes_the_smaller_figure_and_an_unschedulable_node_stays_unschedulable():
    other = fuller()
    other.nodes[1].schedulable = False

    enriched, _ = enrich_scheduling_facts(bare(), "shop", analysis(other))

    assert enriched.nodes[0].cpu_available == pytest.approx(3.4) and enriched.nodes[0].memory_available == pytest.approx(7.25)
    assert enriched.nodes[1].schedulable is False


def test_an_unknown_capacity_is_never_filled_in_from_the_fuller_analysis_which_reports_it_as_zero():
    unknown = bare()
    unknown.nodes[1].cpu_available = unknown.nodes[1].memory_available = None
    lossy = fuller()
    lossy.nodes[1].cpu_available = lossy.nodes[1].memory_available = 0.0      # what analyze_live_topology does to an unreadable figure

    enriched, _ = enrich_scheduling_facts(unknown, "shop", analysis(lossy))

    assert enriched.nodes[1].cpu_available is None and enriched.nodes[1].memory_available is None
    assert enriched.nodes[0].cpu_available == pytest.approx(3.4)          # a known figure is still reduced to the smaller one


def test_nothing_the_evaluated_graph_already_observed_is_overwritten_and_placement_is_never_touched():
    own = bare()
    own.nodes[0].labels, own.nodes[0].ready = {"kubernetes.io/hostname": "w1", "tier": "own"}, False
    own.services[0].declared_intent = DeclaredPlacementIntent(declared_replicas=2, selector={"app": "mine"})
    pods_before = [[(p.name, p.node, p.ready) for p in s.pods] for s in own.services]

    enriched, _ = enrich_scheduling_facts(own, "shop", analysis(fuller()))

    assert enriched.nodes[0].labels["tier"] == "own" and enriched.nodes[0].ready is False
    assert enriched.services[0].declared_intent.selector == {"app": "mine"}
    assert [[(p.name, p.node, p.ready) for p in s.pods] for s in enriched.services] == pods_before


def test_if_component_2_cannot_be_read_again_nothing_is_enriched_and_the_reason_is_reported():
    def down(namespace):
        raise EvidenceError("CLUSTER_CONNECTION_FAILED", "unreachable", http_status=502, component="deployment_topology")

    graph = bare()
    enriched, report = enrich_scheduling_facts(graph, "shop", down)

    assert enriched is graph and report["unavailable"] == "CLUSTER_CONNECTION_FAILED" and report["nodesEnriched"] == []
    assert graph.nodes[0].ready is None and graph.services[0].declared_intent is None


# --- taints: nobody supplies them, so they are read ----------------------------------------------------------------------------------

class Nodes:
    def __init__(self, nodes=None, error=None):
        self.nodes, self.error, self.paths = nodes or [], error, []

    def list_objects(self, resource, path):
        self.paths.append((resource, path))
        if self.error:
            raise self.error
        return self.nodes


def api_node(name, taints=None, spec=True):
    body = {"metadata": {"name": name}}
    if spec:
        body["spec"] = {"taints": taints} if taints is not None else {}
    return body


def test_taints_are_read_as_key_value_effect_and_a_node_with_none_is_observed_empty():
    source = Nodes([api_node("w1", [{"key": "dedicated", "value": "reviews", "effect": "NoSchedule", "timeAdded": "x"}]),
                    api_node("w2")])

    taints = read_node_taints(source)

    assert taints == {"w1": [{"key": "dedicated", "value": "reviews", "effect": "NoSchedule"}], "w2": []}
    assert source.paths == [("nodes", "/api/v1/nodes")]          # one read, of nodes, nothing else


def test_a_node_without_a_spec_or_with_an_unreadable_taint_is_left_unread_not_called_taint_free():
    source = Nodes([api_node("a", spec=False), api_node("b", [{"key": "x"}]), api_node("c", [{"key": "k", "effect": "NoExecute"}])])

    assert read_node_taints(source) == {"c": [{"key": "k", "value": None, "effect": "NoExecute"}]}


def test_the_live_evidence_carries_the_read_taints_and_the_graphs_own_win():
    graph = bare()

    evidence = live_scheduling_evidence(graph, Nodes([api_node("w1", [{"key": "k", "effect": "NoSchedule"}]), api_node("w2")]))

    assert evidence.node_taints["w1"][0]["key"] == "k" and evidence.node_taints["w2"] == []


def test_if_the_nodes_cannot_be_read_the_taints_stay_unknown_and_are_never_defaulted():
    evidence = live_scheduling_evidence(bare(), Nodes(error=ConnectionFailedError("ConnectError")))

    assert dict(evidence.node_taints) == {}
