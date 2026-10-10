from app.engine.deployment_topology_modeling.manifest_parsing.constants import DEFAULT_CPU_LIMIT, TARGET_KIND

def test_constants_maintain_academic_baselines():
    """Ensure hardcoded constants match Phase 2 design specs."""
    assert DEFAULT_CPU_LIMIT == "4.0"
    assert TARGET_KIND == "Deployment"
