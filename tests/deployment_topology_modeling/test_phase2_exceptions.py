import pytest
from app.engine.deployment_topology_modeling.manifest_parsing.exceptions import MissingSchemaError, InvalidTopologyConstraintError, ManifestParsingError

def test_exception_inheritance():
    """Prove custom exceptions correctly inherit from our base error class."""
    assert issubclass(MissingSchemaError, ManifestParsingError)
    assert issubclass(InvalidTopologyConstraintError, ManifestParsingError)
