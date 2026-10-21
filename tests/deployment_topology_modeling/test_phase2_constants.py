from app.engine.deployment_topology_modeling.manifest_parsing.constants import (
    DEFAULT_CPU_LIMIT,
    DEFAULT_MEMORY_LIMIT,
    TARGET_KIND,
)


def test_missing_limits_are_not_invented():
    """A container without limits must stay 'no limit', never a made-up value."""
    assert DEFAULT_CPU_LIMIT is None
    assert DEFAULT_MEMORY_LIMIT is None
    assert TARGET_KIND == "Deployment"
