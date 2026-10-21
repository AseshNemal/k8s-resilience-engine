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


def _write_new_json(
    path: Path,
    payload: Mapping[str, object],
) -> Path:
    created = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            json.dump(payload, stream, indent=2)
            stream.write("\n")
    except FileExistsError as error:
        raise ValueError(
            f"refusing to overwrite existing validation input: {path}"
        ) from error
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise
    return path


def capture_prediction_input(
    *,
    base_url: str,
    workflow_id: str,
    failed_node: str,
    namespace: str,
    output: Path,
    prediction_fetcher: PredictionFetcher = fetch_live_prediction,
) -> Path:
    """Capture the live prediction before the controlled failure begins."""

    if output.exists():
        raise ValueError(
            f"refusing to overwrite existing validation input: {output}"
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
    return _write_new_json(
        output,
        prediction.model_dump(by_alias=True, mode="json"),
    )


def record_evidence_input(
    *,
    experiment_id: str,
    failed_node: str,
    observation_source: str,
    observed_at: str,
    expected_services: Sequence[str],
    service_results: Mapping[str, str],
    expected_workflows: Sequence[str],
    workflow_results: Mapping[str, str],
    output: Path,
    workflow_freeze_id: str | None = None,
    deployment_snapshot_id: str | None = None,
    namespace: str | None = None,
) -> Path:
    """Record complete observations after the controlled failure."""

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
    return _write_new_json(output, evidence)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prediction = commands.add_parser(
        "capture-prediction",
        help="Save the live prediction before failure injection",
    )
    prediction.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
    )
    prediction.add_argument("--workflow-id", required=True)
    prediction.add_argument("--failed-node", required=True)
    prediction.add_argument("--namespace", default="default")
    prediction.add_argument("--output", required=True, type=Path)

    evidence = commands.add_parser(
        "record-evidence",
        help="Save observed outcomes after the controlled failure",
    )
    evidence.add_argument("--failed-node", required=True)
    evidence.add_argument("--namespace", default="default")
    evidence.add_argument("--experiment-id", required=True)
    evidence.add_argument("--observation-source", required=True)
    evidence.add_argument(
        "--observed-at",
        required=True,
        help="ISO 8601 experiment observation time including its timezone",
    )
    evidence.add_argument("--workflow-freeze-id")
    evidence.add_argument("--deployment-snapshot-id")
    evidence.add_argument(
        "--expected-service",
        action="append",
        required=True,
        dest="expected_services",
    )
    evidence.add_argument(
        "--service-result",
        action="append",
        required=True,
        help="Observed NAME=SERVICE_AVAILABLE or NAME=SERVICE_UNAVAILABLE",
    )
    evidence.add_argument(
        "--expected-workflow",
        action="append",
        required=True,
        dest="expected_workflows",
    )
    evidence.add_argument(
        "--workflow-result",
        action="append",
        required=True,
        help="Observed NAME=WORKFLOW_SUCCEEDED or NAME=WORKFLOW_FAILED",
    )
    evidence.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Capture one stage of a validation experiment."""

    arguments = _argument_parser().parse_args(argv)
    if arguments.command == "capture-prediction":
        path = capture_prediction_input(
            base_url=arguments.base_url,
            workflow_id=arguments.workflow_id,
            failed_node=arguments.failed_node,
            namespace=arguments.namespace,
            output=arguments.output,
        )
        result = {"prediction": str(path)}
    else:
        path = record_evidence_input(
            experiment_id=arguments.experiment_id,
            failed_node=arguments.failed_node,
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
            namespace=arguments.namespace,
            output=arguments.output,
        )
        result = {"evidence": str(path)}
    print(json.dumps(result, indent=2))
    return 0


def cli(argv: Sequence[str] | None = None) -> int:
    """Return a concise command-line error for expected capture failures."""

    try:
        return main(argv)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
