from typing import List
from app.engine.deployment_topology_modeling.manifest_parsing.manifest_reader import read_yaml_manifests
from app.engine.deployment_topology_modeling.manifest_parsing.manifest_parser import (
    parse_declared_intents,
    parse_declared_services,
)
from app.engine.deployment_topology_modeling.manifest_parsing.manifest_models import (
    DeclaredIntent,
    DeclaredService,
)


def run_manifest_pipeline(directory: str = "deploy/test-workloads") -> List[DeclaredIntent]:
    """Declared Deployment intent from a manifest file or directory."""
    # read the YAML files -> keep the Deployments -> turn each one into a DeclaredIntent
    return parse_declared_intents(read_yaml_manifests(directory))


def run_service_manifest_pipeline(directory: str = "deploy/test-workloads") -> List[DeclaredService]:
    """Declared Service selectors from a manifest file or directory."""
    return parse_declared_services(read_yaml_manifests(directory))
