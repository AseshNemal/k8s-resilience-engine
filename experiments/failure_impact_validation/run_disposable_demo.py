"""Generate disposable upstream inputs and run Component 3 in one command."""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.failure_impact_validation.generate_disposable_inputs import (  # noqa: E402
    write_disposable_inputs,
)
from experiments.failure_impact_validation.run_disposable_scenarios import (  # noqa: E402
    DEPLOYMENT_FILE,
    METADATA_FILE,
    WORKFLOW_FILE,
    format_compact_summary,
    run_disposable_scenarios,
)


RESULT_FILE = "scenario-results.json"


def run_disposable_demo(
    *,
    scenario_id: str,
    output_dir: Path,
    failed_nodes: Sequence[str],
    generated_at: datetime,
) -> dict[str, object]:
    """Create one complete, write-once synthetic demonstration bundle."""

    targets = tuple(
        output_dir / name
        for name in (
            WORKFLOW_FILE,
            DEPLOYMENT_FILE,
            METADATA_FILE,
            RESULT_FILE,
        )
    )
    existing = tuple(str(path) for path in targets if path.exists())
    if existing:
        raise ValueError(
            "refusing to overwrite existing disposable demo files: "
            + ", ".join(existing)
        )

    write_disposable_inputs(
        output_dir,
        scenario_id,
        generated_at=generated_at,
    )
    return run_disposable_scenarios(
        input_dir=output_dir,
        failed_nodes=failed_nodes,
        output=output_dir / RESULT_FILE,
        generated_at=generated_at,
    )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario-id",
        required=True,
        help="Short identifier embedded in the disposable graph IDs",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="New bundle directory for inputs and scenario-results.json",
    )
    parser.add_argument(
        "--failed-node",
        action="append",
        default=[],
        help="Node to fail; repeat for several nodes or omit to evaluate all nodes",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print a concise terminal table; saved artifacts remain JSON",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Generate, evaluate, save, and print one disposable demo bundle."""

    arguments = _argument_parser().parse_args(argv)
    artifact = run_disposable_demo(
        scenario_id=arguments.scenario_id,
        output_dir=arguments.output_dir,
        failed_nodes=arguments.failed_node,
        generated_at=datetime.now(timezone.utc),
    )
    if arguments.compact:
        print(format_compact_summary(artifact))
    else:
        print(json.dumps(artifact, indent=2))
    return 0


def cli(argv: Sequence[str] | None = None) -> int:
    """Return concise messages for expected disposable demo failures."""

    try:
        return main(argv)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
