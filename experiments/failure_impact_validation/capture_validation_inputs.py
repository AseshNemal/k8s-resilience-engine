"""Capture real validation inputs from the backend and explicit observations.

This research-only command performs a read-only GET against the running
application. It does not inject failures or make Kubernetes changes.
"""

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.failure_impact_prediction.validation_manifest import (  # noqa: E402
    parse_captured_experiment_manifest,
    require_complete_manifest_evidence,
)
from app.shared.schemas.failure_impact import FailureImpactReport  # noqa: E402


PredictionFetcher = Callable[..., Mapping[str, object]]


def _runtime_prediction_url(
    base_url: str,
    *,
    workflow_id: str,
    failed_node: str,
    namespace: str,
) -> str:
    normalized_base = base_url.rstrip("/")
    parsed = urlsplit(normalized_base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be an absolute HTTP or HTTPS URL")
    query = urlencode(
        {
            "workflow_id": workflow_id,
            "failed_node": failed_node,
            "namespace": namespace,
        }
    )
    return f"{normalized_base}/api/failure-impacts/runtime?{query}"


def fetch_live_prediction(
    *,
    base_url: str,
    workflow_id: str,
    failed_node: str,
    namespace: str,
    timeout_seconds: float = 15.0,
) -> dict[str, object]:
    """Fetch and validate one live FailureImpactReport from the backend."""

    url = _runtime_prediction_url(
        base_url,
        workflow_id=workflow_id,
        failed_node=failed_node,
        namespace=namespace,
    )
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            body = response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise ValueError(
            f"runtime prediction request returned HTTP {error.code}{suffix}"
        ) from error
    except URLError as error:
        raise ValueError(
            f"could not reach the runtime prediction endpoint: {error.reason}"
        ) from error

    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(
            "runtime prediction endpoint did not return valid UTF-8 JSON"
        ) from error
    if not isinstance(payload, dict):
        raise ValueError("runtime prediction endpoint must return a JSON object")

    report = FailureImpactReport.model_validate(payload)
    if report.workflow_id != workflow_id:
        raise ValueError("runtime prediction returned a different workflow")
    if report.failed_node != failed_node:
        raise ValueError("runtime prediction returned a different failed node")
    return report.model_dump(by_alias=True, mode="json")


def _result_mapping(
    values: Sequence[str],
    *,
    argument_name: str,
) -> dict[str, str]:
    results: dict[str, str] = {}
    for value in values:
        name, separator, result = value.partition("=")
        name = name.strip()
        result = result.strip()
        if not separator or not name or not result:
            raise ValueError(
                f"{argument_name} must use NAME=RESULT format"
            )
        if name in results:
            raise ValueError(f"{argument_name} repeats '{name}'")
        results[name] = result
    return results


def build_evidence_manifest(
    *,
    experiment_id: str,
    failed_node: str,
    observation_source: str,
    observed_at: str,
    expected_services: Sequence[str],
    service_results: Mapping[str, str],
    expected_workflows: Sequence[str],
    workflow_results: Mapping[str, str],
    workflow_freeze_id: str | None = None,
    deployment_snapshot_id: str | None = None,
    namespace: str | None = None,
) -> dict[str, object]:
    """Build a complete manifest without converting unknowns into successes."""

    payload: dict[str, object] = {
        "experimentId": experiment_id,
        "failedNode": failed_node,
        "observationSource": observation_source,
        "observedAt": observed_at,
        "expectedServices": list(expected_services),
        "serviceResults": dict(service_results),
        "expectedWorkflows": list(expected_workflows),
        "workflowResults": dict(workflow_results),
    }
    optional_fields = {
        "workflowFreezeId": workflow_freeze_id,
        "deploymentSnapshotId": deployment_snapshot_id,
        "namespace": namespace,
    }
    payload.update(
        {key: value for key, value in optional_fields.items() if value is not None}
    )
    manifest = parse_captured_experiment_manifest(payload)
    require_complete_manifest_evidence(manifest)
    return payload


def _check_output_paths(prediction_output: Path, evidence_output: Path) -> None:
    if prediction_output.resolve() == evidence_output.resolve():
        raise ValueError("prediction and evidence outputs must be different files")
    existing = [
        str(path)
        for path in (prediction_output, evidence_output)
        if path.exists()
    ]
    if existing:
        raise ValueError(
            "refusing to overwrite existing validation input: "
            + ", ".join(existing)
        )


def _write_json_pair(
    prediction_output: Path,
    prediction: Mapping[str, object],
    evidence_output: Path,
    evidence: Mapping[str, object],
) -> None:
    created: list[Path] = []
    try:
        for path, payload in (
            (prediction_output, prediction),
            (evidence_output, evidence),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                created.append(path)
                json.dump(payload, stream, indent=2)
                stream.write("\n")
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def prepare_validation_inputs(
    *,
    base_url: str,
    workflow_id: str,
    failed_node: str,
    namespace: str,
    experiment_id: str,
    observation_source: str,
    observed_at: str,
    expected_services: Sequence[str],
    service_results: Mapping[str, str],
    expected_workflows: Sequence[str],
    workflow_results: Mapping[str, str],
    prediction_output: Path,
    evidence_output: Path,
    workflow_freeze_id: str | None = None,
    deployment_snapshot_id: str | None = None,
    prediction_fetcher: PredictionFetcher = fetch_live_prediction,
) -> tuple[Path, Path]:
    """Validate all inputs before creating the two new artifact files."""

    _check_output_paths(prediction_output, evidence_output)
    evidence = build_evidence_manifest(
        experiment_id=experiment_id,
        failed_node=failed_node,
        observation_source=observation_source,
        observed_at=observed_at,
        expected_services=expected_services,
        service_results=service_results,
        expected_workflows=expected_workflows,
        workflow_results=workflow_results,
        workflow_freeze_id=workflow_freeze_id,
        deployment_snapshot_id=deployment_snapshot_id,
        namespace=namespace,
    )
    prediction_payload = prediction_fetcher(
        base_url=base_url,
        workflow_id=workflow_id,
        failed_node=failed_node,
        namespace=namespace,
    )
    prediction = FailureImpactReport.model_validate(prediction_payload)
    if prediction.workflow_id != workflow_id:
        raise ValueError("runtime prediction returned a different workflow")
    if prediction.failed_node != failed_node:
        raise ValueError("runtime prediction returned a different failed node")

    _write_json_pair(
        prediction_output,
        prediction.model_dump(by_alias=True, mode="json"),
        evidence_output,
        evidence,
    )
    return prediction_output, evidence_output


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--failed-node", required=True)
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--observation-source", required=True)
    parser.add_argument(
        "--observed-at",
        required=True,
        help="ISO 8601 experiment observation time including its timezone",
    )
    parser.add_argument("--workflow-freeze-id")
    parser.add_argument("--deployment-snapshot-id")
    parser.add_argument(
        "--expected-service",
        action="append",
        required=True,
        dest="expected_services",
    )
    parser.add_argument(
        "--service-result",
        action="append",
        required=True,
        help="Observed NAME=SERVICE_AVAILABLE or NAME=SERVICE_UNAVAILABLE",
    )
    parser.add_argument(
        "--expected-workflow",
        action="append",
        required=True,
        dest="expected_workflows",
    )
    parser.add_argument(
        "--workflow-result",
        action="append",
        required=True,
        help="Observed NAME=WORKFLOW_SUCCEEDED or NAME=WORKFLOW_FAILED",
    )
    parser.add_argument("--prediction-output", required=True, type=Path)
    parser.add_argument("--evidence-output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Capture a validated pair of files and print their locations."""

    arguments = _argument_parser().parse_args(argv)
    prediction_path, evidence_path = prepare_validation_inputs(
        base_url=arguments.base_url,
        workflow_id=arguments.workflow_id,
        failed_node=arguments.failed_node,
        namespace=arguments.namespace,
        experiment_id=arguments.experiment_id,
        observation_source=arguments.observation_source,
        observed_at=arguments.observed_at,
        expected_services=arguments.expected_services,
        service_results=_result_mapping(
            arguments.service_result,
            argument_name="--service-result",
        ),
        expected_workflows=arguments.expected_workflows,
        workflow_results=_result_mapping(
            arguments.workflow_result,
            argument_name="--workflow-result",
        ),
        workflow_freeze_id=arguments.workflow_freeze_id,
        deployment_snapshot_id=arguments.deployment_snapshot_id,
        prediction_output=arguments.prediction_output,
        evidence_output=arguments.evidence_output,
    )
    print(
        json.dumps(
            {
                "prediction": str(prediction_path),
                "evidence": str(evidence_path),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
