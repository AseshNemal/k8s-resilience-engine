"""The test environment must make ambient kubeconfig access impossible without blocking explicit use."""

import os

import pytest

from app.integrations.kubernetes import client as kube_client, rest


def test_default_lookup_finds_nothing():
    assert not os.path.exists(os.environ["KUBECONFIG"])
    assert "/.kube" not in os.path.expanduser("~")


def test_reading_the_real_users_kubeconfig_fails_loudly(real_kube_dir):
    with pytest.raises(AssertionError, match="real kubeconfig"):
        rest.read_kubeconfig_file(str(real_kube_dir / "config"))
    with pytest.raises(AssertionError, match="real kubeconfig"):
        rest.load_connection([str(real_kube_dir / "config")])


def test_the_guard_covers_what_the_application_calls_not_only_the_helper(real_kube_dir, monkeypatch):
    """KubernetesApiSource reaches the file through load_connection, so pointing it at the real kube
    directory must fail the test rather than quietly use the developer's cluster."""
    monkeypatch.setattr(rest.os.path, "isfile", lambda path: True)  # pretend the real file exists
    with pytest.raises(AssertionError, match="real kubeconfig"):
        kube_client.KubernetesApiSource(config_file=str(real_kube_dir / "config")).list_nodes()


def test_a_kubeconfig_supplied_by_the_test_still_works(tmp_path):
    path = tmp_path / "kubeconfig"
    path.write_text(
        "apiVersion: v1\nkind: Config\ncurrent-context: c\n"
        "clusters:\n- name: k\n  cluster:\n    server: https://127.0.0.1:1\n"
        "users:\n- name: u\n  user:\n    token: t\n"
        "contexts:\n- name: c\n  context:\n    cluster: k\n    user: u\n"
    )
    assert rest.load_connection([str(path)]).server == "https://127.0.0.1:1"


@pytest.mark.live_cluster
def test_live_tests_are_skipped_unless_opted_in():
    assert os.environ.get("RESILIENCE_TEST_KUBECONFIG")
