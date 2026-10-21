import json

import pytest

from app.engine.failure_impact_prediction.captured_evidence_combiner import (
    IncompleteCapturedExperimentError,
)
from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.service import predict_failure_impact
from experiments.failure_impact_validation import capture_validation_inputs
from experiments.failure_impact_validation.capture_validation_inputs import (
    _result_mapping,
    _runtime_prediction_url,
    capture_prediction_input,
    cli,
    fetch_live_prediction,
    main,
    record_evidence_input,
)
from experiments.failure_impact_validation.run_validation import (
    evaluate_saved_experiment,
)


def _prediction_payload() -> dict[str, object]:
    workflow, deployment = get_demo_inputs()
    return predict_failure_impact(
        workflow,
        deployment,
        "node-1",
    ).model_dump(by_alias=True, mode="json")


def _capture(tmp_path, **overrides):
    arguments = {
        "base_url": "http://127.0.0.1:8000",
        "workflow_id": "checkout",
        "failed_node": "node-1",
        "namespace": "research",
        "output": tmp_path / "prediction.json",
        "prediction_fetcher": lambda **_: _prediction_payload(),
    }
    arguments.update(overrides)
    return capture_prediction_input(**arguments)


def _record(tmp_path, **overrides):
    arguments = {
        "experiment_id": "captured-node-1-failure",
        "failed_node": "node-1",
        "observation_source": "connected-cluster probes",
        "observed_at": "2026-10-21T05:00:00Z",
        "expected_services": ["cartservice"],
        "service_results": {"cartservice": "SERVICE_UNAVAILABLE"},
        "expected_workflows": ["checkout"],
        "workflow_results": {"checkout": "WORKFLOW_FAILED"},
        "output": tmp_path / "evidence.json",
        "workflow_freeze_id": "checkout-freeze-v1",
        "deployment_snapshot_id": "deployment-before-node-1",
        "namespace": "research",
    }
    arguments.update(overrides)
    return record_evidence_input(**arguments)


def test_staged_capture_writes_input_shapes_accepted_by_runner(tmp_path) -> None:
    prediction_path = _capture(tmp_path)
    evidence_path = _record(tmp_path)

    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    artifact = evaluate_saved_experiment(
        batch_id="capture-round-trip",
        prediction_payload=prediction,
        evidence_payload=evidence,
    )

    assert prediction["workflowId"] == "checkout"
    assert evidence["observationSource"] == "connected-cluster probes"
    assert artifact["validation"]["exactMatchCount"] == 1


def test_record_rejects_missing_observation_without_writing(tmp_path) -> None:
    with pytest.raises(IncompleteCapturedExperimentError):
        _record(tmp_path, workflow_results={})

    assert not (tmp_path / "evidence.json").exists()


def test_capture_refuses_to_overwrite_before_fetching(tmp_path) -> None:
    prediction_path = tmp_path / "prediction.json"
    prediction_path.write_text("keep", encoding="utf-8")
    called = False

    def fetcher(**_):
        nonlocal called
        called = True
        return _prediction_payload()

    with pytest.raises(ValueError, match="refusing to overwrite"):
        _capture(tmp_path, prediction_fetcher=fetcher)

    assert prediction_path.read_text(encoding="utf-8") == "keep"
    assert called is False


def test_capture_rejects_prediction_for_another_node(tmp_path) -> None:
    payload = _prediction_payload()
    payload["failedNode"] = "node-2"

    with pytest.raises(ValueError, match="different failed node"):
        _capture(tmp_path, prediction_fetcher=lambda **_: payload)

    assert not (tmp_path / "prediction.json").exists()


def test_runtime_url_encodes_query_values() -> None:
    url = _runtime_prediction_url(
        "http://127.0.0.1:8000/",
        workflow_id="checkout flow",
        failed_node="worker/1",
        namespace="research team",
    )

    assert url == (
        "http://127.0.0.1:8000/api/failure-impacts/runtime?"
        "workflow_id=checkout+flow&failed_node=worker%2F1&"
        "namespace=research+team"
    )


def test_fetch_live_prediction_uses_read_only_runtime_endpoint(
    monkeypatch,
) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return json.dumps(_prediction_payload()).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["method"] = request.get_method()
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(capture_validation_inputs, "urlopen", fake_urlopen)

    prediction = fetch_live_prediction(
        base_url="http://127.0.0.1:8000",
        workflow_id="checkout",
        failed_node="node-1",
        namespace="research",
        timeout_seconds=4.0,
    )

    assert captured == {
        "method": "GET",
        "url": (
            "http://127.0.0.1:8000/api/failure-impacts/runtime?"
            "workflow_id=checkout&failed_node=node-1&namespace=research"
        ),
        "timeout": 4.0,
    }
    assert prediction["failedNode"] == "node-1"


def test_result_mapping_rejects_duplicates_and_malformed_values() -> None:
    assert _result_mapping(
        ["payment=SERVICE_UNAVAILABLE"],
        argument_name="--service-result",
    ) == {"payment": "SERVICE_UNAVAILABLE"}

    with pytest.raises(ValueError, match="repeats 'payment'"):
        _result_mapping(
            [
                "payment=SERVICE_AVAILABLE",
                "payment=SERVICE_UNAVAILABLE",
            ],
            argument_name="--service-result",
        )
    with pytest.raises(ValueError, match="NAME=RESULT"):
        _result_mapping(
            ["payment"],
            argument_name="--service-result",
        )


def test_capture_command_reports_invalid_backend_without_traceback(
    tmp_path,
    capsys,
) -> None:
    exit_code = cli(
        [
            "capture-prediction",
            "--base-url",
            "not-a-url",
            "--workflow-id",
            "checkout",
            "--failed-node",
            "node-1",
            "--namespace",
            "research",
            "--output",
            str(tmp_path / "prediction.json"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "error: base_url must be an absolute HTTP or HTTPS URL" in (
        captured.err
    )
    assert "Traceback" not in captured.err
    assert captured.out == ""
    assert not (tmp_path / "prediction.json").exists()


def test_record_evidence_command_does_not_contact_backend(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    def unexpected_request(*_, **__):
        raise AssertionError("record-evidence must not contact the backend")

    monkeypatch.setattr(
        capture_validation_inputs,
        "urlopen",
        unexpected_request,
    )
    evidence_path = tmp_path / "evidence.json"

    exit_code = main(
        [
            "record-evidence",
            "--failed-node",
            "node-1",
            "--namespace",
            "research",
            "--experiment-id",
            "post-failure-run",
            "--observation-source",
            "connected-cluster probes",
            "--observed-at",
            "2026-10-22T01:00:00Z",
            "--expected-service",
            "cartservice",
            "--service-result",
            "cartservice=SERVICE_UNAVAILABLE",
            "--expected-workflow",
            "checkout",
            "--workflow-result",
            "checkout=WORKFLOW_FAILED",
            "--output",
            str(evidence_path),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output == {"evidence": str(evidence_path)}
    assert json.loads(evidence_path.read_text(encoding="utf-8"))[
        "experimentId"
    ] == "post-failure-run"
