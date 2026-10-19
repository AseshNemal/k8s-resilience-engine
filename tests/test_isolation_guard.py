"""The test environment must make ambient kubeconfig access impossible without blocking explicit use."""

import os

import pytest
from kubernetes import config


def test_default_lookup_finds_nothing():
    assert not os.path.exists(os.environ["KUBECONFIG"])
    assert "/.kube" not in os.path.expanduser("~")


def test_reading_the_real_users_kubeconfig_fails_loudly(real_kube_dir):
    with pytest.raises(AssertionError, match="real kubeconfig"):
        config.load_kube_config(config_file=str(real_kube_dir / "config"))
    with pytest.raises(AssertionError, match="real kubeconfig"):
        config.new_client_from_config(config_file=str(real_kube_dir / "config"))


def test_a_kubeconfig_supplied_by_the_test_still_works(tmp_path):
    path = tmp_path / "kubeconfig"
    path.write_text(
        "apiVersion: v1\nkind: Config\ncurrent-context: c\n"
        "clusters:\n- name: k\n  cluster:\n    server: https://127.0.0.1:1\n"
        "users:\n- name: u\n  user:\n    token: t\n"
        "contexts:\n- name: c\n  context:\n    cluster: k\n    user: u\n"
    )
    api_client = config.new_client_from_config(config_file=str(path))
    try:
        assert api_client.configuration.host == "https://127.0.0.1:1"
    finally:
        api_client.close()


@pytest.mark.live_cluster
def test_live_tests_are_skipped_unless_opted_in():
    assert os.environ.get("RESILIENCE_TEST_KUBECONFIG")
