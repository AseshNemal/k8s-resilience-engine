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
    # spec.replicas defaults to 1 in Kubernetes itself; limits have no default and stay None.
    assert intent.expected_replicas == 1
    assert intent.resource_limits.cpu is None
    assert intent.resource_limits.memory is None
    assert len(intent.spread_constraints) == 0
    assert intent.has_node_affinity is False
    assert intent.namespace == "default"


def test_parse_spread_constraint_keeps_all_fields_and_flags_missing_required():
    manifests = [{
        "kind": "Deployment",
        "metadata": {"name": "pay", "namespace": "shop"},
        "spec": {"template": {"spec": {
            "containers": [{"name": "c"}],
            "topologySpreadConstraints": [
                {"maxSkew": 1, "topologyKey": "kubernetes.io/hostname",
                 "whenUnsatisfiable": "DoNotSchedule", "minDomains": 3,
                 "labelSelector": {"matchLabels": {"app": "pay"}}},
                {"topologyKey": "kubernetes.io/hostname"},
            ],
        }}},
    }]
    intent = parse_declared_intents(manifests)[0]
    assert intent.namespace == "shop"
    full, partial = intent.spread_constraints
    assert full.min_domains == 3
    assert full.label_selector == {"matchLabels": {"app": "pay"}}
    # Missing required fields are not invented; they are reported.
    assert partial.max_skew is None
    assert len(intent.parse_warnings) == 1
    assert "maxSkew" in intent.parse_warnings[0]


def test_parse_declared_services():
    from app.engine.deployment_topology_modeling.manifest_parsing.manifest_parser import parse_declared_services
    services = parse_declared_services([
        {"kind": "Service", "metadata": {"name": "paymentservice", "namespace": "resilience-test"},
         "spec": {"selector": {"app": "paymentservice"}}},
        {"kind": "Deployment", "metadata": {"name": "x"}},
    ])
    assert [(s.name, s.namespace, s.selector) for s in services] == [
        ("paymentservice", "resilience-test", {"app": "paymentservice"})
    ]
