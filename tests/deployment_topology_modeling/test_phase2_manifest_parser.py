import pytest
from app.engine.deployment_topology_modeling.manifest_parsing.manifest_parser import parse_declared_intents

def test_parse_standard_deployment_intent():
    # Construct a raw dictionary simulating read_yaml_manifests output
    mock_manifests = [
        {
            "kind": "Deployment",
            "metadata": {"name": "frontend-service"},
            "spec": {
                "replicas": 5,
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "frontend",
                                "resources": {
                                    "limits": {
                                        "cpu": "2.5",
                                        "memory": "4Gi"
                                    }
                                }
                            }
                        ],
                        "topologySpreadConstraints": [
                            {"maxSkew": 2, "topologyKey": "kubernetes.io/zone"}
                        ],
                        "affinity": {
                            "nodeAffinity": {}
                        }
                    }
                }
            }
        },
        {
            "kind": "Service", # Should be ignored because it is not a Deployment
            "metadata": {"name": "ignore-me"}
        }
    ]
    
    intents = parse_declared_intents(mock_manifests)
    
    assert len(intents) == 1
    intent = intents[0]
    
    assert intent.service_name == "frontend-service"
    assert intent.expected_replicas == 5
    assert intent.resource_limits.cpu == "2.5"
    assert intent.resource_limits.memory == "4Gi"
    
    assert len(intent.spread_constraints) == 1
    assert intent.spread_constraints[0].max_skew == 2
    assert intent.spread_constraints[0].topology_key == "kubernetes.io/zone"
    assert intent.has_node_affinity is True
    
def test_parse_missing_fields_default_gracefully():
    mock_manifests = [
        {
            "kind": "Deployment",
            "metadata": {"name": "bare-service"},
            "spec": {}
        }
    ]
    
    intents = parse_declared_intents(mock_manifests)
    intent = intents[0]
    
    assert intent.service_name == "bare-service"
    # Should fallback to safe Phase 2 defaults
    assert intent.expected_replicas == 1
    assert intent.resource_limits.cpu == "4.0"
    assert len(intent.spread_constraints) == 0
    assert intent.has_node_affinity is False
