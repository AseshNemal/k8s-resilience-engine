from typing import Dict, Any
from app.engine.deployment_topology_modeling.manifest_parsing.constants import TARGET_KIND
from app.engine.deployment_topology_modeling.manifest_parsing.exceptions import MissingSchemaError

def validate_manifest_kind(manifest: Dict[str, Any]) -> bool:
    """Validates if the dictionary represents a targeted deployment."""
    if not manifest:
        return False
    kind = manifest.get("kind", "")
    return kind == TARGET_KIND

def fail_if_missing_metadata(manifest: Dict[str, Any]) -> None:
    """Strictly validates core architecture blocks."""
    if "metadata" not in manifest:
        raise MissingSchemaError("Manifest is missing fundamental metadata block.")
