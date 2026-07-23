import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DATA_DIR = PROJECT_ROOT / "sample_data"


def load_sample_json(filename: str) -> dict[str, Any]:
    file_path = SAMPLE_DATA_DIR / filename

    if not file_path.exists():
        raise FileNotFoundError(
            f"Sample data file not found: {file_path}"
        )

    with file_path.open("r", encoding="utf-8") as file:
        return json.load(file)