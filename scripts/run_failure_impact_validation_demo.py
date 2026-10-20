"""Print the synthetic Component 3 validation demonstration as JSON."""

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.failure_impact_prediction.validation_demo import (  # noqa: E402
    build_synthetic_validation_demo,
)


def main() -> None:
    """Render the explicitly synthetic validation evidence to stdout."""

    print(json.dumps(build_synthetic_validation_demo(), indent=2))


if __name__ == "__main__":
    main()
