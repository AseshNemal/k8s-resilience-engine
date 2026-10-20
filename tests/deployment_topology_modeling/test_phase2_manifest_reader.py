from pathlib import Path

from app.engine.deployment_topology_modeling.manifest_parsing.manifest_reader import (
    read_yaml_manifests,
    read_yaml_manifests_with_report,
)

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

    # Parses as a plain YAML string, so it holds no Kubernetes object.
    not_k8s = d / "not-k8s.yaml"
    not_k8s.write_text(":[unbalanced{broken\nyaml")

    # Real YAML syntax error.
    corrupted_yaml = d / "corrupt.yaml"
    corrupted_yaml.write_text("key: [unclosed\n  other: {\n")

    yml_file = d / "second.yml"
    yml_file.write_text("kind: Service\nmetadata:\n  name: mock-svc\n")

    manifests, skipped = read_yaml_manifests_with_report(str(d))

    # Both valid documents are read (.yaml and .yml); bad files are reported, not silently hidden.
    assert sorted(m["kind"] for m in manifests) == ["Deployment", "Service"]
    assert sorted(Path(f).name for f, _ in skipped) == ["corrupt.yaml", "not-k8s.yaml"]
    assert len(read_yaml_manifests(str(d))) == 2
