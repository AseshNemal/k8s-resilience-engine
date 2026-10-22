"""Live Experiments tab: stored KinD run results are served as stored, and absence is a 404."""
import json

import pytest
from fastapi.testclient import TestClient

from app.engine.deployment_topology_modeling import service
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _condition(scenario_id, config, exposed):
    stat = {"n": 2, "mean": 0.25 if exposed else 0.0, "sd": 0.0, "ci95": [0.25, 0.25] if exposed else [0.0, 0.0]}
    zero = {"n": 2, "mean": 0.0, "sd": 0.0, "ci95": [0.0, 0.0]}
    return {
        "scenario_id": scenario_id, "config": config, "runs": 2, "failed_runs": 0, "unstable_runs": 1,
        "d_rate": stat, "exposure_rate_t0": zero, "exposure_rate_t1": stat,
        "compliance_rate_t1": {"n": 0, "mean": None, "sd": None, "ci95": None},
        "target_became_exposed": 2 if exposed else 0,
        "target_status_t1_counts": {"EXPOSED" if exposed else "NOT_EXPOSED": 2},
        "observation_ms": zero,
    }


def test_results_are_returned_as_stored(tmp_path):
    (tmp_path / "summary.json").write_text(json.dumps({
        "results_dir": str(tmp_path), "total_runs": 4,
        "conditions": [_condition("08_constrained_resources", "A-no-rule", True),
                       _condition("05_pod_deletion", "A-no-rule", False)],
    }), encoding="utf-8")

    data = service.get_live_experiment_results(tmp_path)

    assert data["execution_mode"] == "LIVE_KIND_CLUSTER"
    assert data["is_experimental_evidence"] is True
    assert "stand-in" in data["evidence_note"]
    assert data["total_runs"] == 4
    assert data["failed_runs"] == 0
    names = {c["scenario_id"]: c["scenario_name"] for c in data["conditions"]}
    assert names == {"08_constrained_resources": "Constrained resources", "05_pod_deletion": "Pod deletion / reschedule"}
    exposed = next(c for c in data["conditions"] if c["scenario_id"] == "08_constrained_resources")
    assert exposed["d_rate"]["mean"] == pytest.approx(0.25)
    assert exposed["target_status_t1_counts"] == {"EXPOSED": 2}


def test_example_run_carries_placement_and_concentration(tmp_path):
    (tmp_path / "summary.json").write_text(json.dumps({
        "results_dir": str(tmp_path), "total_runs": 1,
        "conditions": [_condition("07_node_drain", "A-no-rule", False)],
    }), encoding="utf-8")
    snap = lambda nodes, conc: {"paymentservice": {  # noqa: E731
        "ready_replicas": 3, "concentrated": False, "replica_concentration": conc, "exposed_failure_domains": [],
        "pods": [[f"p{i}", n, True] for i, n in enumerate(nodes)]}}
    (tmp_path / "07_node_drain__A-no-rule__run1.json").write_text(json.dumps({
        "repetition": 1, "action": {"action": "kubectl drain w1"}, "stable_after_disruption": True,
        "target_status_t0": "NOT_EXPOSED", "target_status_t1": "NOT_EXPOSED",
        "t0": snap(["w1", "w2", "w3"], 0.3333), "t1": snap(["w2", "w2", "w3"], 0.6667),
    }), encoding="utf-8")

    run = service.get_live_experiment_results(tmp_path)["conditions"][0]["example_run"]

    assert [p["node"] for p in run["t1"]["paymentservice"]] == ["w2", "w2", "w3"]
    assert run["concentration_t0"]["paymentservice"]["ratio"] == pytest.approx(0.3333)
    assert run["concentration_t1"]["paymentservice"] == {"ratio": pytest.approx(0.6667), "ready_replicas": 3}


def test_missing_results_return_none(tmp_path):
    assert service.get_live_experiment_results(tmp_path) is None


def test_endpoint_404_without_results(client, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "LIVE_EXPERIMENT_RESULTS_DIR", tmp_path)
    resp = client.get("/api/deployments/benchmarks/live-experiments")
    assert resp.status_code == 404


def test_endpoint_serves_stored_results(client, tmp_path, monkeypatch):
    (tmp_path / "summary.json").write_text(json.dumps({
        "results_dir": str(tmp_path), "total_runs": 2,
        "conditions": [_condition("01_initial_deployment", "B-spread", False)],
    }), encoding="utf-8")
    monkeypatch.setattr(service, "LIVE_EXPERIMENT_RESULTS_DIR", tmp_path)
    data = client.get("/api/deployments/benchmarks/live-experiments").json()
    assert data["conditions"][0]["config"] == "B-spread"
    assert data["conditions"][0]["unstable_runs"] == 1


def test_page_has_live_experiments_tab(client):
    html = client.get("/components/deployments").text
    assert 'id="tab-btn-live-experiments"' in html
    assert 'id="tab-live-experiments"' in html
    assert "deployments_live_experiments_widget.js" in html


def test_bookinfo_results_name_their_own_workload_and_target(tmp_path):
    (tmp_path / "summary.json").write_text(json.dumps({
        "results_dir": "bookinfo-five-repetitions", "total_runs": 2,
        "conditions": [_condition("08_constrained_resources", "A-no-rule", True)],
    }), encoding="utf-8")

    data = service.get_live_experiment_results(tmp_path, workload="bookinfo")

    assert data["workload"] == "bookinfo"
    assert data["target_service"] == "reviews"
    assert data["services"] == ["details", "ratings", "reviews", "productpage"]
    assert "Bookinfo" in data["evidence_note"] and "bookinfo-exp" in data["evidence_note"]


def test_unknown_workload_is_refused():
    with pytest.raises(ValueError):
        service.get_live_experiment_results(workload="boutique")


def test_endpoint_serves_the_requested_workload(client, tmp_path, monkeypatch):
    standin, bookinfo = tmp_path / "standin", tmp_path / "bookinfo"
    for folder, scenario in ((standin, "01_initial_deployment"), (bookinfo, "08_constrained_resources")):
        folder.mkdir()
        (folder / "summary.json").write_text(json.dumps({
            "results_dir": folder.name, "total_runs": 2, "conditions": [_condition(scenario, "A-no-rule", False)],
        }), encoding="utf-8")
    monkeypatch.setattr(service, "LIVE_EXPERIMENT_RESULTS_DIR", standin)
    monkeypatch.setattr(service, "LIVE_EXPERIMENT_BOOKINFO_DIR", bookinfo)

    default = client.get("/api/deployments/benchmarks/live-experiments").json()
    real_app = client.get("/api/deployments/benchmarks/live-experiments?workload=bookinfo").json()

    assert (default["workload"], default["target_service"]) == ("standin", "paymentservice")
    assert default["conditions"][0]["scenario_id"] == "01_initial_deployment"
    assert (real_app["workload"], real_app["target_service"]) == ("bookinfo", "reviews")
    assert real_app["conditions"][0]["scenario_id"] == "08_constrained_resources"
    assert real_app["available_workloads"] == ["standin", "bookinfo"]
    assert client.get("/api/deployments/benchmarks/live-experiments?workload=other").status_code == 422
