import pytest
from app.engine.deployment_topology_modeling.manifest_parsing.validators import validate_manifest_kind, fail_if_missing_metadata
from app.engine.deployment_topology_modeling.manifest_parsing.exceptions import MissingSchemaError

def test_validate_manifest_kind_deployment():
    assert validate_manifest_kind({"kind": "Deployment"}) is True
    assert validate_manifest_kind({"kind": "Service"}) is False

def test_fail_if_missing_metadata_throws_correctly():
    with pytest.raises(MissingSchemaError):
        fail_if_missing_metadata({"kind": "Deployment"}) # Missing metadata key
        
    # Should not raise exception
    fail_if_missing_metadata({"metadata": {"name": "test"}})
