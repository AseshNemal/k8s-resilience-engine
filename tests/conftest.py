"""Shared pytest configuration.

Unit tests must never reach a real Kubernetes cluster through the developer's ambient kubeconfig.
This file does two things, neither of which blocks a test that supplies its own kubeconfig:

* every test runs with HOME and KUBECONFIG redirected to an empty temporary location, so the default
  lookup (``$KUBECONFIG`` or ``~/.kube/config``) finds nothing;
* the one function that reads a kubeconfig from disk (``rest.read_kubeconfig_file``) refuses, with an
  AssertionError, to read anything inside the real user's kube directory, even when a test names that
  path explicitly.

Real-cluster tests are opt-in: mark them ``@pytest.mark.live_cluster`` and set
``RESILIENCE_TEST_KUBECONFIG`` to a kubeconfig for a disposable test cluster.
"""

import os
from pathlib import Path

import pytest
from app.integrations.kubernetes import rest

REAL_KUBE_DIR = (Path.home() / ".kube").resolve()
LIVE_KUBECONFIG_ENV = "RESILIENCE_TEST_KUBECONFIG"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_cluster: talks to a real Kubernetes cluster; runs only when "
        f"{LIVE_KUBECONFIG_ENV} points at a test kubeconfig",
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get(LIVE_KUBECONFIG_ENV):
        return
    skip = pytest.mark.skip(reason=f"live cluster tests are opt-in: set {LIVE_KUBECONFIG_ENV}")
    for item in items:
        if "live_cluster" in item.keywords:
            item.add_marker(skip)


def _inside_real_kube_dir(raw_path: str) -> bool:
    resolved = Path(os.path.expanduser(raw_path)).resolve()
    return resolved == REAL_KUBE_DIR or REAL_KUBE_DIR in resolved.parents


def _guarded(original):
    def wrapper(path, *args, **kwargs):
        if _inside_real_kube_dir(str(path)):
            raise AssertionError(
                f"a test tried to read the real kubeconfig at {path}; "
                "use a fake cluster source or a kubeconfig written under tmp_path"
            )
        return original(path, *args, **kwargs)

    wrapper.__wrapped__ = original
    return wrapper


@pytest.fixture(autouse=True)
def _isolate_kubernetes_environment(monkeypatch, tmp_path_factory):
    sandbox = tmp_path_factory.mktemp("kube-sandbox")
    monkeypatch.setenv("HOME", str(sandbox))
    monkeypatch.setenv("KUBECONFIG", str(sandbox / "no-kubeconfig-here"))
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    live = os.environ.get(LIVE_KUBECONFIG_ENV)
    original = rest.read_kubeconfig_file
    if hasattr(original, "__wrapped__"):
        original = original.__wrapped__
    monkeypatch.setattr(rest, "read_kubeconfig_file", _guarded(original))
    if live:
        # Opt-in tests receive their kubeconfig explicitly; nothing else gets it.
        monkeypatch.setenv("RESILIENCE_LIVE_KUBECONFIG", live)


@pytest.fixture
def real_kube_dir() -> Path:
    return REAL_KUBE_DIR


@pytest.fixture
def use_cluster(monkeypatch):
    """Install a fake cluster as the only source discovery can use: ``use_cluster(source)``."""

    def install(source):
        from app.engine.deployment_topology_modeling import discovery

        monkeypatch.setattr(discovery, "get_cluster_source", lambda: source)
        return source

    return install
