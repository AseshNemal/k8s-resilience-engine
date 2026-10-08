import pytest
from unittest.mock import patch
from app.engine.deployment_topology_modeling.manifest_parsing.pipeline import run_manifest_pipeline
from app.engine.deployment_topology_modeling.manifest_parsing.manifest_models import DeclaredIntent

@patch("app.engine.deployment_topology_modeling.manifest_parsing.pipeline.read_yaml_manifests")
@patch("app.engine.deployment_topology_modeling.manifest_parsing.pipeline.parse_declared_intents")
def test_pipeline_orchestration(mock_parse, mock_read):
    """Proves the pipeline accurately connects the reader and parser."""
    mock_read.return_value = [{"raw": "dictionary"}]
    mock_intent = DeclaredIntent(service_name="test")
    mock_parse.return_value = [mock_intent]
    
    results = run_manifest_pipeline("fake/dir")
    
    assert len(results) == 1
    assert results[0].service_name == "test"
    mock_read.assert_called_once_with("fake/dir")
    mock_parse.assert_called_once_with([{"raw": "dictionary"}])
