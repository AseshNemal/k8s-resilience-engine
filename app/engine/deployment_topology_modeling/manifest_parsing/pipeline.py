from typing import List, Dict, Any
from app.engine.deployment_topology_modeling.manifest_parsing.manifest_reader import read_yaml_manifests
from app.engine.deployment_topology_modeling.manifest_parsing.manifest_parser import parse_declared_intents
from app.engine.deployment_topology_modeling.manifest_parsing.manifest_models import DeclaredIntent

def run_manifest_pipeline(directory: str = "deploy/test-workloads") -> List[DeclaredIntent]:
    """
    Orchestrates the File I/O reader and Semantic Parser securely.
    Acts as the entrypoint for Phase 3 drift analysis hooks.
    """
    raw_manifests = read_yaml_manifests(directory)
    intents = parse_declared_intents(raw_manifests)
    return intents
