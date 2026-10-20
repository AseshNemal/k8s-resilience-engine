import logging
import os
from pathlib import Path
from typing import List, Dict, Any

import yaml

logger = logging.getLogger(__name__)

MANIFEST_SUFFIXES = (".yaml", ".yml")


def read_yaml_manifests_with_report(
    directory_path: str = "deploy/test-workloads",
) -> tuple[List[Dict[str, Any]], List[tuple[str, str]]]:
    """Reads every YAML document from a file or directory.

    Returns (documents, skipped) where skipped lists (file, reason) for each file that could not
    be parsed, so a malformed manifest is never dropped without trace.
    """
    target_path = Path(directory_path)
    if not target_path.is_absolute():
        target_path = Path(os.getcwd()) / directory_path
    if not target_path.exists():
        return [], []

    if target_path.is_file():
        files = [target_path]
    else:
        files = sorted(p for p in target_path.iterdir() if p.suffix in MANIFEST_SUFFIXES)

    manifests: List[Dict[str, Any]] = []
    skipped: List[tuple[str, str]] = []
    for file_path in files:
        if file_path.name.startswith("."):
            continue
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                docs = list(yaml.safe_load_all(f))
        except (OSError, yaml.YAMLError) as exc:
            skipped.append((str(file_path), str(exc)))
            logger.warning("Skipped unreadable manifest %s: %s", file_path, exc)
            continue
        objects = [doc for doc in docs if isinstance(doc, dict)]
        if not objects and any(doc is not None for doc in docs):
            # Parses as YAML but holds no Kubernetes object (e.g. a bare string from broken syntax).
            skipped.append((str(file_path), "file contains no YAML mapping / Kubernetes object"))
            logger.warning("Skipped manifest without Kubernetes objects: %s", file_path)
        manifests.extend(objects)
    return manifests, skipped


def read_yaml_manifests(directory_path: str = "deploy/test-workloads") -> List[Dict[str, Any]]:
    """Documents only; skipped files are logged as warnings."""
    manifests, _ = read_yaml_manifests_with_report(directory_path)
    return manifests
