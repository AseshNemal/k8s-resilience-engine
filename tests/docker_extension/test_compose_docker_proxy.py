"""The Docker socket is mounted into the restricted proxy only; the backend has none and reaches Docker through the proxy's socket."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "extension" / "compose.yaml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]
BACKEND, PROXY = SERVICES["resilience-engine"], SERVICES["docker-proxy"]


def mounts(service):
    return [str(v) for v in service.get("volumes", [])]


def test_only_the_proxy_has_the_docker_socket_and_the_backend_has_no_other_way_to_it():
    assert any("docker.sock" in m for m in mounts(PROXY))
    for name, service in SERVICES.items():
        if name != "docker-proxy":
            assert not any("docker.sock" in m for m in mounts(service)), f"{name} must not mount the Docker socket"
            assert not any("docker.sock" in str(value) for value in (service.get("environment") or {}).values()), name


def test_the_backend_and_the_proxy_meet_on_one_unix_socket_in_a_shared_volume_and_publish_no_port():
    assert any(m.startswith("docker-proxy-socket:") for m in mounts(BACKEND)) and any(m.startswith("docker-proxy-socket:") for m in mounts(PROXY))
    assert BACKEND["environment"]["RESILIENCE_DOCKER_PROXY_SOCKET"].startswith("/run/docker-proxy/")
    assert "ports" not in BACKEND and "ports" not in PROXY


def test_the_proxy_runs_the_policy_app_with_every_capability_dropped_and_a_read_only_filesystem():
    command = " ".join(PROXY["entrypoint"])

    assert "app.integrations.docker.proxy_app:app" in command and "--uds /run/docker-proxy/proxy.sock" in command
    assert PROXY["cap_drop"] == ["ALL"] and PROXY["read_only"] is True and "no-new-privileges:true" in PROXY["security_opt"]
    assert "cap_add" not in PROXY


def test_the_proxys_allowed_clusters_are_configured_where_the_backend_cannot_change_them():
    clusters = PROXY["environment"]["RESILIENCE_PROXY_ALLOWED_CLUSTERS"].split(",")

    assert "resilience-validation" in clusters
    assert "RESILIENCE_PROXY_ALLOWED_CLUSTERS" not in BACKEND["environment"]


def test_the_probe_reaches_the_application_through_the_hosts_gateway_and_the_arm_flag_is_the_backends_alone():
    assert BACKEND["environment"]["RESILIENCE_PROBE_HOST_GATEWAY"] == "host.docker.internal"
    assert BACKEND["environment"]["RESILIENCE_ALLOW_CLUSTER_WRITES"] == "1"
    assert "RESILIENCE_ALLOW_CLUSTER_WRITES" not in PROXY["environment"]


def test_the_module_the_proxy_runs_exists_and_the_image_ships_the_application_package():
    assert (ROOT / "app" / "integrations" / "docker" / "proxy_app.py").is_file()
    dockerfile = (ROOT / "extension" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY app" in dockerfile
