"""Ground truth: per-service failure outcomes, owner resolution and pooling of several recordings.

Each test copies the stored KinD recording (experiments/single-node-failure/concentrated-baseline,
3 paymentservice pods on resilience-research-worker, worker failed) and adds what a newer recording holds.
"""
import json
import shutil
from pathlib import Path

import pytest

from app.engine.deployment_topology_modeling.ground_truth_evaluation import (
    KIND_CONCENTRATED_BASELINE,
    evaluate_recordings,
    evaluate_snapshot,
    recording_dirs,
)

RS_UID = "16af80cf-0a81-4c3a-91d4-fee73755cab5"  # the ReplicaSet the baseline pods point to
PODS = ["paymentservice-74d644658f-rmmc5", "paymentservice-74d644658f-rw5hq", "paymentservice-74d644658f-tgzzd"]


@pytest.fixture
def recording(tmp_path) -> Path:
    target = tmp_path / "recording"
    shutil.copytree(KIND_CONCENTRATED_BASELINE, target)
    return target


def add_owner_objects(recording: Path, owner_truth: dict[str, str]) -> None:
    (recording / "deployments.json").write_text(json.dumps({"items": [
        {"metadata": {"name": "paymentservice", "uid": "dep-1"}, "spec": {"replicas": 3,
         "template": {"metadata": {"labels": {"app": "paymentservice"}}}}}]}), encoding="utf-8")
    (recording / "replicasets.json").write_text(json.dumps({"items": [
        {"metadata": {"name": "paymentservice-74d644658f", "uid": RS_UID,
                      "ownerReferences": [{"kind": "Deployment", "name": "paymentservice", "uid": "dep-1", "controller": True}]}}]}),
        encoding="utf-8")
    (recording / "owner-truth.txt").write_text("".join(f"{d} {p}\n" for p, d in owner_truth.items()), encoding="utf-8")


def test_owner_resolution_is_not_scored_without_the_owner_recording(recording):
    assert evaluate_snapshot(recording).certificate.owner_resolution_accuracy is None


def test_owner_resolution_is_scored_against_the_selector_query(recording):
    add_owner_objects(recording, {pod: "paymentservice" for pod in PODS})
    ev = evaluate_snapshot(recording)
    assert ev.counts["owner"] == (3, 3)
    assert ev.certificate.owner_resolution_accuracy == pytest.approx(1.0)


def test_an_owner_the_api_server_disagrees_with_is_an_error(recording):
    add_owner_objects(recording, {PODS[0]: "paymentservice", PODS[1]: "paymentservice", PODS[2]: "other"})
    assert evaluate_snapshot(recording).counts["owner"] == (2, 3)


def test_per_service_outcomes_label_only_the_services_they_name(recording):
    (recording / "worker1-failure" / "service-result.txt").write_text(
        "paymentservice SERVICE_UNAVAILABLE\nunrelated SERVICE_AVAILABLE\n", encoding="utf-8")
    (check,) = evaluate_snapshot(recording).exposure_checks
    assert (check["service"], check["observed_available"], check["predicted_available"]) == ("paymentservice", False, False)


def test_a_service_with_no_recorded_outcome_is_not_a_labelled_case(recording):
    (recording / "worker1-failure" / "service-result.txt").write_text("unrelated SERVICE_AVAILABLE\n", encoding="utf-8")
    ev = evaluate_snapshot(recording)
    assert ev.exposure_checks == []
    assert ev.certificate.exposure_detection_accuracy is None


def test_recordings_are_pooled_by_counts(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    shutil.copytree(KIND_CONCENTRATED_BASELINE, first)
    shutil.copytree(KIND_CONCENTRATED_BASELINE, second)
    # In the second recording the model is told the service survived: a wrong label, so one miss.
    (second / "worker1-failure" / "service-result.txt").write_text("SERVICE_AVAILABLE\n", encoding="utf-8")

    pooled = evaluate_recordings(recording_dirs(tmp_path))

    assert [r["source"] for r in pooled.recordings] == ["a", "b"]
    assert pooled.counts["pod_node"] == (6, 6)
    assert pooled.counts["exposure"] == (1, 2)
    assert pooled.certificate.exposure_detection_accuracy == pytest.approx(0.5)
    assert pooled.confusion == {"down_predicted_down": 1, "down_predicted_up": 0, "up_predicted_up": 0, "up_predicted_down": 1}
    assert pooled.certificate.is_statistically_sound is False
