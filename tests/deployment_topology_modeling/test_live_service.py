"""Component 2 facade: live graph, drift, declared state and snapshots all use real evidence only."""

import shutil
from pathlib import Path

import pytest

from app.engine.deployment_topology_modeling import service
from app.engine.deployment_topology_modeling.discovery import DiscoveryError
from app.integrations.kubernetes.client import ConnectionFailedError, NotConnectedError
from app.shared.evidence_errors import EvidenceError
from tests.support.fake_cluster import FakeClusterSource, deployment, node, pod, two_node_shop

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def fresh_snapshots():
    service.clear_cluster_snapshots()
    yield
    service.clear_cluster_snapshots()


def concentrated_payment_cluster() -> FakeClusterSource:
    return FakeClusterSource(
        nodes=[node("w1"), node("w2")],
        pods={"default": [pod(f"paymentservice-{i}", "w1", app="paymentservice") for i in range(3)]},
        deployments={"default": [deployment("paymentservice", 3)]},
    )


# ---- live graph ------------------------------------------------------------------------------------

def test_live_graph_describes_exactly_the_connected_cluster(use_cluster):
    use_cluster(two_node_shop())

    graph = service.get_live_deployment_graph(namespace="shop")

    assert {n.name for n in graph.nodes} == {"worker-1", "worker-2"}
    assert {s.name for s in graph.services} == {"web", "db"}
    assert graph.validation.service_pod_mapping_accuracy is None
    assert graph.validation.runtime_overhead_ms >= 0.0


@pytest.mark.parametrize("failure", [NotConnectedError("x"), ConnectionFailedError("y")])
def test_every_live_facade_fails_instead_of_falling_back(use_cluster, failure):
    use_cluster(FakeClusterSource(error=failure))

    calls = [
        service.get_live_deployment_graph,
        service.analyze_resilience_drift,
        service.get_cluster_zones,
        service.get_drift_spofs,
        service.get_drift_categories_summary,
        service.get_concentration_rankings,
        service.get_node_risk_rankings,
        service.get_chaos_safety_status,
        lambda namespace="default": service.freeze_cluster_state("s", namespace),
        lambda namespace="default": service.get_node_detail("worker-1", namespace),
    ]
    for call in calls:
        with pytest.raises(DiscoveryError):
            call(namespace="default")


def test_validate_chaos_action_reports_failed_discovery_as_not_allowed(use_cluster):
    use_cluster(FakeClusterSource(error=NotConnectedError("x")))

    outcome = service.validate_chaos_action(namespace="default")

    assert outcome["action_allowed"] is False


def test_discover_live_exposes_the_envelope(use_cluster):
    use_cluster(two_node_shop())

    result = service.discover_live("shop")

    assert result.status == "ok"
    assert result.declared_state == "unavailable"
    assert all(isinstance(s.drift_detected, bool) for s in result.graph.services)


# ---- declared state --------------------------------------------------------------------------------

def test_without_a_declared_source_compliance_is_unknown_not_perfect(use_cluster):
    use_cluster(concentrated_payment_cluster())

    report, _ = service.analyze_resilience_drift()

    assert report.constraint_compliance_rate is None
    assert report.declared_state == "unavailable"
    assert report.total_services == 1  # actual-placement analysis still works
    assert report.mean_concentration == pytest.approx(1.0)
    assert report.exposed_service_count == 1


def test_with_an_explicit_declared_source_compliance_is_computed(use_cluster, tmp_path):
    use_cluster(concentrated_payment_cluster())
    declared = tmp_path / "declared"
    declared.mkdir()
    shutil.copy(PROJECT_ROOT / "deploy/test-workloads/concentrated-paymentservice.yaml", declared)

    report, _ = service.analyze_resilience_drift(manifest_path=str(declared))

    assert report.declared_state == "available"
    assert isinstance(report.constraint_compliance_rate, float)


def test_an_explicit_but_missing_declared_source_is_an_error(use_cluster, tmp_path):
    use_cluster(concentrated_payment_cluster())

    with pytest.raises(EvidenceError) as caught:
        service.analyze_resilience_drift(manifest_path=str(tmp_path / "nope"))

    assert caught.value.code == "DECLARED_STATE_UNAVAILABLE"


def test_manifest_intent_needs_an_explicit_source():
    with pytest.raises(EvidenceError) as caught:
        service.get_manifest_intent()

    assert (caught.value.code, caught.value.http_status) == ("DECLARED_STATE_UNAVAILABLE", 409)


def test_no_default_manifest_is_ever_read(use_cluster, monkeypatch):
    use_cluster(concentrated_payment_cluster())
    monkeypatch.setattr(service, "run_manifest_pipeline",
                        lambda *a, **k: pytest.fail("a default manifest source was read"))

    service.get_live_deployment_graph()
    service.analyze_resilience_drift()


# ---- snapshots -------------------------------------------------------------------------------------

def test_a_frozen_snapshot_records_where_and_when_it_came_from(use_cluster):
    use_cluster(two_node_shop())

    frozen = service.freeze_cluster_state("before", namespace="shop")

    provenance = frozen["provenance"]
    assert provenance["connectionFingerprint"] == "fake-connection"
    assert provenance["identityVerified"] is False
    assert provenance["historical"] is False
    assert provenance["capturedAt"].endswith("Z")
    assert {p["zone"] for p in frozen["pods"]} <= {"zone-a", "zone-b"}


def test_unknown_zone_stays_unknown_in_a_snapshot(use_cluster):
    use_cluster(FakeClusterSource(
        nodes=[node("w1", None)],
        pods={"default": [pod("a-1", "w1", app="a")]},
        deployments={"default": [deployment("a", 1)]},
    ))

    frozen = service.freeze_cluster_state("z")

    assert frozen["pods"][0]["zone"] is None


def test_a_stored_snapshot_is_labelled_historical_with_its_provenance(use_cluster):
    use_cluster(two_node_shop())
    service.freeze_cluster_state("before", namespace="shop")

    stored = service.get_cluster_snapshot("before", "MANUAL")

    assert stored["provenance"]["historical"] is True
    assert stored["provenance"]["connectionFingerprint"] == "fake-connection"
    assert service.list_cluster_snapshots() == ["before_MANUAL"]


def test_snapshots_are_not_served_after_the_connection_changes(use_cluster):
    first = two_node_shop()
    use_cluster(first)
    service.freeze_cluster_state("before", namespace="shop")

    second = two_node_shop()
    second.connection_fingerprint = lambda: "another-connection"
    use_cluster(second)

    assert service.list_cluster_snapshots() == []
    assert service.get_cluster_snapshot("before", "MANUAL") is None


def test_snapshots_cannot_be_read_when_no_connection_can_vouch_for_them(use_cluster):
    use_cluster(two_node_shop())
    service.freeze_cluster_state("before", namespace="shop")

    gone = two_node_shop()
    gone.connection_fingerprint = lambda: None
    use_cluster(gone)

    with pytest.raises(EvidenceError) as caught:
        service.list_cluster_snapshots()
    assert caught.value.code == "SNAPSHOT_PROVENANCE_UNVERIFIED"
    with pytest.raises(EvidenceError):
        service.get_cluster_snapshot("before", "MANUAL")


def test_a_snapshot_without_recorded_provenance_is_refused(use_cluster):
    use_cluster(two_node_shop())
    service._GLOBAL_SNAPSHOT_REPO.save(  # noqa: SLF001 - simulating a legacy/unknown-origin entry
        scenario_id="legacy", phase_tag="T0",
        snapshot=service.ClusterStateFreezer.freeze("legacy", raw_pods=[], nodes=[]),
    )

    with pytest.raises(EvidenceError) as caught:
        service.get_cluster_snapshot("legacy", "T0")

    assert caught.value.code == "SNAPSHOT_PROVENANCE_UNVERIFIED"
