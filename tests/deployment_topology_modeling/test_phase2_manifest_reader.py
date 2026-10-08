import pytest
from app.engine.deployment_topology_modeling.manifest_parsing.manifest_reader import read_yaml_manifests

def test_reader_ignores_non_existent_folder():
    """Test standard fallback when targeting an empty/invalid config dir."""
    manifests = read_yaml_manifests("fake_folder_that_does_not_exist")
    assert len(manifests) == 0

def test_reader_finds_actual_workloads(tmp_path):
    """Test reading a mock yaml file safely."""
    # We create a temporary safe directory to mimic deploy/test-workloads
    d = tmp_path / "mock-deploy"
    d.mkdir()
    
    mock_yaml = d / "fake-service.yaml"
    mock_yaml.write_text("kind: Deployment\nmetadata:\n  name: mock-svc\n")
    
    hidden_file = d / ".fake-hidden.yaml"
    hidden_file.write_text("should_be: ignored")

    corrupted_yaml = d / "corrupt.yaml"
    corrupted_yaml.write_text(":[unbalanced{broken\nyaml")

    manifests = read_yaml_manifests(str(d))
    
    # It should only extract the one valid document gracefully
    assert len(manifests) == 1
    assert manifests[0]["kind"] == "Deployment"
    assert manifests[0]["metadata"]["name"] == "mock-svc"
