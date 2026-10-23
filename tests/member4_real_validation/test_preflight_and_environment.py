"""The preflight checklist and the files that make the test environment reproducible."""

import json
from pathlib import Path

import pytest
import yaml

from experiments.member4_real_validation import preflight as pf
from experiments.member4_real_validation import resource_assessment as ra
from experiments.member4_real_validation.preflight import FAIL, PASS, PENDING, WARN, run_preflight
from tests.support.member4_fake_apiserver import KUBE_SYSTEM_UID, FakeApiServer, NAMESPACE, write_kubeconfig

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "experiments" / "member4_real_validation"
GIB, MIB = ra.GIB, ra.MIB

ROOMY = ra.Facts("arm64", 16 * GIB, 12 * GIB, 8, {"x-control-plane": GIB, "x-worker": 500 * MIB}, ("resilience-validation",))
TIGHT = ra.Facts("arm64", 8 * GIB, 4107005952, 5, {"desktop-control-plane": 813 * MIB, "desktop-worker": 377 * MIB,
                                                   "desktop-worker2": 561 * MIB}, ("desktop",))


def designation_file(tmp_path, **overrides):
    values = {"name": "kind-resilience-validation", "apiServer": "https://127.0.0.1:41234", "clusterUid": KUBE_SYSTEM_UID,
              "namespace": NAMESPACE, "designatedBy": "asesh", "context": "kind-resilience-validation",
              "kindCluster": "resilience-validation", "workers": ["resilience-validation-worker"]}
    path = tmp_path / "designation.json"
    path.write_text(json.dumps({**values, **overrides}))
    return str(path)


def by_name(checks):
    return {c.name: c for c in checks if c.name != "person"}


def test_with_no_cluster_no_designation_and_a_tight_machine_nothing_is_ready_and_the_reasons_are_given(tmp_path):
    checks = run_preflight(None, None, facts=lambda: TIGHT)
    named = by_name(checks)

    assert named["resources"].status == FAIL and named["designation"].status == FAIL and named["credentials"].status == FAIL
    assert named["cluster read"].status == PENDING and "does not exist yet" in named["designation"].detail
    assert sum(c.status == PENDING for c in checks if c.name == "person") == len(pf.PERSON_STEPS) >= 5


def test_the_example_designation_is_not_usable_as_it_stands():
    with pytest.raises(ValueError) as raised:
        pf.load_designation(str(FOLDER / "designation.example.json"))

    assert "placeholders" in str(raised.value)


def test_a_complete_designation_on_a_roomy_machine_with_the_cluster_present_passes_every_automated_check(tmp_path):
    kubeconfig = write_kubeconfig(tmp_path / "own.kubeconfig")

    checks = run_preflight(designation_file(tmp_path), kubeconfig, recovery_dir=str(tmp_path / "recovery"), facts=lambda: ROOMY,
                           app_memory_mib=1024)
    named = by_name(checks)

    assert [named[n].status for n in ("resources", "designation", "kind cluster", "recovery", "credentials")] == [PASS] * 5


def test_a_kind_cluster_that_does_not_exist_or_is_docker_desktop_fails(tmp_path):
    kubeconfig = write_kubeconfig(tmp_path / "own.kubeconfig")

    absent = by_name(run_preflight(designation_file(tmp_path, kindCluster="not-created"), kubeconfig, facts=lambda: ROOMY))
    desktop = by_name(run_preflight(designation_file(tmp_path, kindCluster="desktop"), kubeconfig, facts=lambda: ROOMY))

    assert absent["kind cluster"].status == FAIL and "does not exist" in absent["kind cluster"].detail
    assert desktop["kind cluster"].status == FAIL and "Docker Desktop" in desktop["kind cluster"].detail


def test_the_default_kubeconfig_fails_the_credentials_check(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".kube").mkdir(parents=True)
    ambient = write_kubeconfig(home / ".kube" / "config")
    monkeypatch.setenv("HOME", str(home))

    assert by_name(run_preflight(None, ambient, facts=lambda: ROOMY))["credentials"].status == FAIL


def test_a_pending_recovery_record_blocks(tmp_path):
    recovery = tmp_path / "recovery"
    recovery.mkdir()
    (recovery / "r.json").write_text(json.dumps({"recordId": "r", "state": "STOPPED", "container": "x-worker", "cluster": "x"}))

    checks = by_name(run_preflight(designation_file(tmp_path), None, recovery_dir=str(recovery), facts=lambda: ROOMY))

    assert checks["recovery"].status == FAIL


def test_checking_the_cluster_is_read_only_and_verifies_identity_against_the_designation(tmp_path):
    api = FakeApiServer(namespace=NAMESPACE)
    kubeconfig = write_kubeconfig(tmp_path / "own.kubeconfig")

    def factory(path):
        from app.engine.deployment_topology_modeling.discovery import discover
        from app.integrations.kubernetes.validation_gateway import KubernetesTestClusterGateway
        return KubernetesTestClusterGateway(path, topology_reader=discover, transport=api.transport)

    checks = by_name(run_preflight(designation_file(tmp_path), kubeconfig, check_cluster=True, facts=lambda: ROOMY,
                                   gateway_factory=factory, app_memory_mib=1024))

    assert checks["cluster read"].status == PASS and "identity matches" in checks["cluster read"].detail
    assert api.methods == {"GET"}


def test_a_cluster_whose_uid_differs_from_the_designation_fails_the_read(tmp_path):
    api = FakeApiServer(namespace=NAMESPACE, uid="someone-elses-cluster")
    kubeconfig = write_kubeconfig(tmp_path / "own.kubeconfig")

    def factory(path):
        from app.engine.deployment_topology_modeling.discovery import discover
        from app.integrations.kubernetes.validation_gateway import KubernetesTestClusterGateway
        return KubernetesTestClusterGateway(path, topology_reader=discover, transport=api.transport)

    check = by_name(run_preflight(designation_file(tmp_path), kubeconfig, check_cluster=True, facts=lambda: ROOMY,
                                  gateway_factory=factory))["cluster read"]

    assert check.status == FAIL and "CLUSTER_IDENTITY_MISMATCH" in check.detail


def test_the_program_exits_non_zero_while_anything_automated_is_failing(capsys):
    code = pf.main([], facts=lambda: TIGHT)

    assert code == 1 and "NOT READY" in capsys.readouterr().out


# --- the files that make the environment reproducible ----------------------------------------------------------------------------

def test_the_kind_config_names_a_cluster_that_is_not_docker_desktop_and_publishes_the_probe_on_the_control_plane_only():
    config = yaml.safe_load((FOLDER / "kind-validation-cluster.yaml").read_text())

    assert config["name"] == "resilience-validation" and config["name"] not in ("desktop", "docker-desktop")
    roles = [n["role"] for n in config["nodes"]]
    assert roles.count("control-plane") == 1 and roles.count("worker") == 2             # the minimal profile
    mappings = [(n["role"], m) for n in config["nodes"] for m in n.get("extraPortMappings", [])]
    assert [role for role, _ in mappings] == ["control-plane"]                          # never on a worker the run kills
    assert mappings[0][1]["listenAddress"] == "127.0.0.1" and mappings[0][1]["containerPort"] == 30080
    assert all(n["image"] == "kindest/node:v1.36.1" for n in config["nodes"])


def test_the_probe_service_matches_the_kind_port_mapping_and_the_probe_url():
    kind = yaml.safe_load((FOLDER / "kind-validation-cluster.yaml").read_text())
    service = yaml.safe_load((FOLDER / "manifests" / "probe-nodeport.yaml").read_text())
    mapping = kind["nodes"][0]["extraPortMappings"][0]

    assert service["spec"]["ports"][0]["nodePort"] == mapping["containerPort"] and service["spec"]["type"] == "NodePort"
    assert service["metadata"]["namespace"] == "resilience-validation"
    assert f"127.0.0.1:{mapping['hostPort']}" in (ROOT / "docs" / "MEMBER4_KIND_VALIDATION_RUNBOOK.md").read_text()


def test_nothing_in_the_environment_files_installs_istio_or_touches_the_default_kubeconfig_context():
    for path in FOLDER.rglob("*.yaml"):
        text = path.read_text().lower()
        assert "istio" not in text.replace("without istio", "") and "kubectl apply" not in text
    config_text = (FOLDER / "kind-validation-cluster.yaml").read_text()
    assert "--kubeconfig" in config_text            # the create command is documented with its own credentials file


def test_the_readiness_tooling_only_ever_reads():
    source = (FOLDER / "preflight.py").read_text() + (FOLDER / "resource_assessment.py").read_text()

    for forbidden in ("kind create", "kind delete", "docker run", "docker kill", "docker start", "patch_deployment(",
                      "restore_deployment(", "allow_writes=True", "writes_enabled=True", "subprocess.Popen", "shell=True"):
        assert forbidden not in source, forbidden


def test_nothing_in_the_product_imports_the_readiness_tooling_or_the_composition_root():
    offenders = []
    for folder in ("app", "extension"):
        for path in (ROOT / folder).rglob("*.py"):
            if "experiments.member4_real_validation" in path.read_text():
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_the_dev_lab_does_not_reach_the_real_validation_stack():
    for path in (ROOT / "experiments" / "member4_dev_lab").rglob("*.py"):
        text = path.read_text()
        assert "member4_real_validation" not in text and "validation_gateway" not in text and "app.integrations.kind" not in text
