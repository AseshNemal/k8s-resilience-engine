import json
from datetime import datetime, timedelta, timezone

import pytest

from app.engine.failure_impact_prediction.baseline_comparison import (
    compare_failure_impact_methods,
)
from app.engine.failure_impact_prediction.baseline_export import (
    build_method_comparison_export,
)
from app.engine.failure_impact_prediction.baseline_manifest import (
    parse_method_comparison_artifact,
)
from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.method_evaluation import (
    IncompleteMethodEvaluationEvidenceError,
)
from app.engine.failure_impact_prediction.method_evaluation_export import (
    ARTIFACT_TYPE,
)
from experiments.failure_impact_validation.run_method_evaluation import (
    cli,
    evaluate_saved_method_artifacts,
    main,
)


COMPARISON_TIME = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)
OBSERVATION_TIME = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
EVALUATION_TIME = datetime(2026, 10, 22, 11, 0, tzinfo=timezone.utc)


def _comparison_payload() -> dict[str, object]:
    workflow, deployment = get_demo_inputs()
    comparison = compare_failure_impact_methods(
        workflow,
        deployment,
        "node-1",
        dependency_scenario_services={"cartservice"},
    )
    return build_method_comparison_export(
        comparison,
        generated_at=COMPARISON_TIME,
    )


def _evidence_payload() -> dict[str, object]:
    return {
        "experimentId": "node-1-method-evaluation",
        "failedNode": "node-1",
        "observationSource": "controlled cluster probes",
        "observedAt": OBSERVATION_TIME.isoformat(),
        "workflowFreezeId": "checkout-freeze-v1",
        "deploymentSnapshotId": "component3-demo-snapshot-v1",
        "namespace": "research",
        "expectedServices": [
            "frontend",
            "paymentservice",
            "cartservice",
            "checkoutservice",
            "emailservice",
        ],
        "serviceResults": {
            "frontend": "SERVICE_AVAILABLE",
            "paymentservice": "SERVICE_AVAILABLE",
            "cartservice": "SERVICE_UNAVAILABLE",
            "checkoutservice": "SERVICE_AVAILABLE",
            "emailservice": "SERVICE_AVAILABLE",
        },
        "expectedWorkflows": ["checkout"],
        "workflowResults": {"checkout": "WORKFLOW_FAILED"},
    }


def test_comparison_artifact_round_trip_preserves_method_claims() -> None:
    parsed = parse_method_comparison_artifact(_comparison_payload())

    assert parsed.generated_at == COMPARISON_TIME
    assert parsed.comparison.failed_node == "node-1"
    assert parsed.comparison.replica_count_only.vulnerable_services == (
        "emailservice",
    )
    assert parsed.comparison.deployment_only.unavailable_services == (
        "cartservice",
    )
    assert parsed.comparison.proposed_cross_layer.failed_workflows == [
        "checkout"
    ]


def test_comparison_parser_rejects_unknown_fields() -> None:
    payload = _comparison_payload()
    payload["unexpected"] = True

    with pytest.raises(ValueError, match="unexpected"):
        parse_method_comparison_artifact(payload)


def test_runner_evaluates_each_method_against_same_observation() -> None:
    artifact = evaluate_saved_method_artifacts(
        comparison_payload=_comparison_payload(),
        evidence_payload=_evidence_payload(),
        evaluated_at=EVALUATION_TIME,
    )

    assert artifact["artifactType"] == ARTIFACT_TYPE
    assert artifact["experimentId"] == "node-1-method-evaluation"
    assert artifact["comparisonGeneratedAt"] == "2026-10-22T09:00:00Z"
    assert artifact["observedAt"] == "2026-10-22T10:00:00Z"
    assert artifact["evaluatedAt"] == "2026-10-22T11:00:00Z"
    methods = artifact["methods"]
    assert isinstance(methods, dict)
    assert methods["replicaCountOnly"]["workflowEvaluation"] is None
    assert methods["deploymentOnly"]["serviceEvaluation"]["exactMatch"] is True
    assert methods["dependencyOnly"]["serviceEvaluation"] is None
    assert methods["proposedCrossLayer"]["serviceEvaluation"]["exactMatch"] is True
    assert methods["proposedCrossLayer"]["workflowEvaluation"]["exactMatch"] is True


def test_runner_requires_matching_deployment_snapshot() -> None:
    evidence = _evidence_payload()
    evidence["deploymentSnapshotId"] = "different-snapshot"

    with pytest.raises(ValueError, match="same deployment snapshot"):
        evaluate_saved_method_artifacts(
            comparison_payload=_comparison_payload(),
            evidence_payload=evidence,
            evaluated_at=EVALUATION_TIME,
        )


def test_runner_requires_comparison_before_observation() -> None:
    evidence = _evidence_payload()
    evidence["observedAt"] = (
        COMPARISON_TIME - timedelta(minutes=1)
    ).isoformat()

    with pytest.raises(ValueError, match="before the observation"):
        evaluate_saved_method_artifacts(
            comparison_payload=_comparison_payload(),
            evidence_payload=evidence,
            evaluated_at=EVALUATION_TIME,
        )


def test_runner_rejects_naive_evaluation_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone information"):
        evaluate_saved_method_artifacts(
            comparison_payload=_comparison_payload(),
            evidence_payload=_evidence_payload(),
            evaluated_at=datetime(2026, 10, 22, 11, 0),
        )


def test_runner_refuses_unprobed_baseline_claim() -> None:
    evidence = _evidence_payload()
    expected = evidence["expectedServices"]
    results = evidence["serviceResults"]
    assert isinstance(expected, list)
    assert isinstance(results, dict)
    expected.remove("emailservice")
    results.pop("emailservice")

    with pytest.raises(
        IncompleteMethodEvaluationEvidenceError,
        match="emailservice",
    ):
        evaluate_saved_method_artifacts(
            comparison_payload=_comparison_payload(),
            evidence_payload=evidence,
            evaluated_at=EVALUATION_TIME,
        )


def test_cli_reads_writes_and_prints_evaluation(tmp_path, capsys) -> None:
    comparison_path = tmp_path / "method-comparison.json"
    evidence_path = tmp_path / "evidence.json"
    output_path = tmp_path / "method-evaluation.json"
    comparison_path.write_text(
        json.dumps(_comparison_payload()),
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps(_evidence_payload()),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--comparison",
            str(comparison_path),
            "--evidence",
            str(evidence_path),
            "--output",
            str(output_path),
        ]
    )
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert saved == printed
    assert saved["artifactType"] == ARTIFACT_TYPE


def test_cli_refuses_to_overwrite_evaluation(tmp_path) -> None:
    comparison_path = tmp_path / "method-comparison.json"
    evidence_path = tmp_path / "evidence.json"
    output_path = tmp_path / "method-evaluation.json"
    comparison_path.write_text(
        json.dumps(_comparison_payload()),
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps(_evidence_payload()),
        encoding="utf-8",
    )
    output_path.write_text("preserve", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        main(
            [
                "--comparison",
                str(comparison_path),
                "--evidence",
                str(evidence_path),
                "--output",
                str(output_path),
            ]
        )

    assert output_path.read_text(encoding="utf-8") == "preserve"


def test_cli_reports_missing_artifact_without_traceback(tmp_path, capsys) -> None:
    exit_code = cli(
        [
            "--comparison",
            str(tmp_path / "missing-comparison.json"),
            "--evidence",
            str(tmp_path / "missing-evidence.json"),
            "--output",
            str(tmp_path / "evaluation.json"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "error: Could not read" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
