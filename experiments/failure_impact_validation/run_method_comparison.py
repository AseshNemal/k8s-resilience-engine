"""Compare failure-impact methods using saved upstream evidence."""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.failure_impact_prediction.baseline_comparison import (  # noqa: E402
    compare_failure_impact_methods,
)
from app.engine.failure_impact_prediction.baseline_export import (  # noqa: E402
    build_method_comparison_export,
    write_method_comparison_artifact,
)
from app.shared.schemas.deployment import DeploymentGraph  # noqa: E402
from app.shared.schemas.workflow import WorkflowGraph  # noqa: E402


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"Could not read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path}: {error.msg}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _scenario_services(values: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        service = value.strip()
        if not service:
            raise ValueError(
                "dependency scenario services must be non-empty strings"
            )
        normalized.append(service)
    if not normalized:
        raise ValueError(
            "at least one dependency scenario service must be declared"
        )
    return tuple(sorted(set(normalized)))


def evaluate_saved_method_comparison(
    *,
    workflow_payload: Mapping[str, object],
    deployment_payload: Mapping[str, object],
    failed_node: str,
    dependency_scenario_services: Sequence[str],
    generated_at: datetime,
) -> dict[str, object]:
    """Validate saved inputs and return one method-comparison artifact."""

    workflow = WorkflowGraph.model_validate(workflow_payload)
    deployment = DeploymentGraph.model_validate(deployment_payload)
    comparison = compare_failure_impact_methods(
        workflow,
        deployment,
        failed_node.strip(),
        dependency_scenario_services=_scenario_services(
            dependency_scenario_services
        ),
    )
    return build_method_comparison_export(
        comparison,
        generated_at=generated_at,
    )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workflow",
        type=Path,
        required=True,
        help="Path to a saved WorkflowGraph JSON object",
    )
    parser.add_argument(
        "--deployment",
        type=Path,
        required=True,
        help="Path to a saved DeploymentGraph JSON object",
    )
    parser.add_argument(
        "--failed-node",
        required=True,
        help="Worker node selected for the declared failure scenario",
    )
    parser.add_argument(
        "--dependency-scenario-service",
        action="append",
        required=True,
        help=(
            "Service declared unavailable in the dependency-only scenario; "
            "repeat for multiple services"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Write a new comparison artifact without overwriting",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Build, save, and print one comparison from real saved evidence."""

    arguments = _argument_parser().parse_args(argv)
    if not arguments.failed_node.strip():
        raise ValueError("failed node must be a non-empty string")

    artifact = evaluate_saved_method_comparison(
        workflow_payload=_read_json_object(arguments.workflow),
        deployment_payload=_read_json_object(arguments.deployment),
        failed_node=arguments.failed_node,
        dependency_scenario_services=(
            arguments.dependency_scenario_service
        ),
        generated_at=datetime.now(timezone.utc),
    )
    write_method_comparison_artifact(arguments.output, artifact)
    print(json.dumps(artifact, indent=2))
    return 0


def cli(argv: Sequence[str] | None = None) -> int:
    """Return concise messages for expected input and evidence failures."""

    try:
        return main(argv)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
