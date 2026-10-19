import pytest
from app.engine.deployment_topology_modeling.manifest_parsing.extractors import extract_cpu_limit, extract_memory_limit

def test_extract_valid_cpu_limit():
    assert extract_cpu_limit({"cpu": "2.5"}) == "2.5"

def test_extract_missing_cpu_limit():
    assert extract_cpu_limit({}) is None

def test_extract_memory_limit():
    assert extract_memory_limit({"memory": "4Gi"}) == "4Gi"
