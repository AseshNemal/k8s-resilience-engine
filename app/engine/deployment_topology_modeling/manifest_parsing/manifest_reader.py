import yaml
import os
from pathlib import Path
from typing import List, Dict, Any

def read_yaml_manifests(directory_path: str = "deploy/test-workloads") -> List[Dict[str, Any]]:
    """
    Safely reads all Kubernetes YAML files from a directory, parsing them into Python dictionaries.
    Ignores non-yaml files and corrupted syntax without crashing the API backend.
    """
    manifests = []
    # Force absolute resolution based on current root execution context (usually project base)
    target_dir = Path(os.getcwd()) / directory_path

    if not target_dir.exists() or not target_dir.is_dir():
        return manifests

    for file_path in target_dir.glob("*.yaml"):
        # Explicit bypass for hidden files
        if file_path.name.startswith("."):
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                # yaml.safe_load_all securely handles multi-document config files (separated by ---)
                docs = yaml.safe_load_all(f)
                for doc in docs:
                    if isinstance(doc, dict):
                        manifests.append(doc)
        except Exception:
            # We silently bypass malformed files as per the Phase 2 definition of done.
            # E.g. If someone puts a corrupted YAML in the folder, the backend stays alive.
            pass

    return manifests
