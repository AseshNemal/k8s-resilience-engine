"""Copying an application to a disposable cluster: portable, honest about what could not be copied, never a Secret."""

import pytest
import yaml

from app.engine.recommendation_evaluation_validation.validation_loop.intent_adapter import ORIGIN, as_analysis, attach_declared_intent
from app.engine.recommendation_evaluation_validation.validation_loop.preparation import PreparationRefused, parse_manual, prepare
from app.engine.recommendation_evaluation_validation.validation_loop.resources import Facts, assess, facts_from_engine
from tests.support.member4_harness import bookinfo_scenario

SOURCE, TARGET = "bookinfo", "resilience-validation-app"


def deployment(name="reviews", *, replicas=3, pod=None, annotations=None):
    return {"metadata": {"name": name, "namespace": SOURCE, "uid": "u-1", "resourceVersion": "9", "generation": 4, "creationTimestamp": "t",
                         "managedFields": [{}], "ownerReferences": [{"kind": "x"}], "labels": {"app": name},
                         "annotations": {"kubectl.kubernetes.io/last-applied-configuration": "{}", "deployment.kubernetes.io/revision": "3", **(annotations or {})}},
            "spec": {"replicas": replicas, "selector": {"matchLabels": {"app": name}},
                     "template": {"metadata": {"labels": {"app": name}, "creationTimestamp": None}, "spec": pod or {"containers": [{"name": "c", "image": "i"}]}}},
            "status": {"readyReplicas": 3}}


def service(name="reviews", **spec):
    return {"metadata": {"name": name, "namespace": SOURCE, "uid": "s-1", "resourceVersion": "2"},
            "spec": {"selector": {"app": name}, "ports": [{"port": 9080, "nodePort": 31000}], "type": "ClusterIP", "clusterIP": "10.0.0.1",
                     "clusterIPs": ["10.0.0.1"], "ipFamilies": ["IPv4"], **spec}}


def plan(**changes):
    options = dict(source_namespace=SOURCE, target_namespace=TARGET, deployments=[deployment()], services=[service()], configmaps=[])
    options.update(changes)
    return prepare(**options)


def by_kind(p, kind):
    return [m for m in p.manifests if m["kind"] == kind]


def test_everything_that_belongs_to_the_source_cluster_is_stripped_and_the_namespace_is_forced():
    p = plan()
    d, s = by_kind(p, "Deployment")[0], by_kind(p, "Service")[0]

    assert d["metadata"]["namespace"] == s["metadata"]["namespace"] == TARGET
    for key in ("uid", "resourceVersion", "generation", "creationTimestamp", "managedFields", "ownerReferences"):
        assert key not in d["metadata"] and key not in s["metadata"]
    assert "status" not in d and "creationTimestamp" not in d["spec"]["template"]["metadata"]
    assert d["metadata"]["annotations"] == {"resilience-validation/copied-from-namespace": SOURCE}
    assert not {"clusterIP", "clusterIPs", "ipFamilies"} & set(s["spec"]) and "nodePort" not in s["spec"]["ports"][0]
    assert d["spec"]["replicas"] == 3 and d["metadata"]["labels"] == {"app": "reviews"}


def test_the_source_objects_are_not_changed():
    source = deployment()
    plan(deployments=[source])

    assert source["metadata"]["uid"] == "u-1" and source["status"] and source["metadata"]["namespace"] == SOURCE


def test_a_load_balancer_becomes_cluster_ip_with_a_note():
    p = plan(services=[service(type="LoadBalancer")])

    assert by_kind(p, "Service")[0]["spec"]["type"] == "ClusterIP" and any("LoadBalancer" in n for n in p.notes)


def test_a_clean_application_is_deployable():
    assert plan().deployable is True and plan().missing == []


@pytest.mark.parametrize("pod, kind", [
    ({"containers": [{"name": "c", "env": [{"name": "P", "valueFrom": {"secretKeyRef": {"name": "db-pass", "key": "p"}}}]}]}, "Secret"),
    ({"containers": [{"name": "c", "envFrom": [{"secretRef": {"name": "all"}}]}]}, "Secret"),
    ({"containers": [{"name": "c"}], "volumes": [{"name": "v", "secret": {"secretName": "tls"}}]}, "Secret"),
    ({"containers": [{"name": "c"}], "imagePullSecrets": [{"name": "regcred"}]}, "Secret"),
    ({"containers": [{"name": "c"}], "volumes": [{"name": "v", "persistentVolumeClaim": {"claimName": "data"}}]}, "PersistentVolumeClaim"),
    ({"containers": [{"name": "c"}], "nodeSelector": {"kubernetes.io/hostname": "worker-1"}}, "nodeSelector"),
])
def test_what_cannot_be_copied_is_listed_and_blocks_the_deployment_until_the_person_resolves_it(pod, kind):
    p = plan(deployments=[deployment(pod=pod)])

    assert [m.kind for m in p.missing] == [kind] and p.deployable is False
    assert p.missing[0].blocking is True and p.missing[0].used_by == "Deployment/reviews"


def test_a_secret_is_never_part_of_the_manifests_and_the_message_says_to_create_it_yourself():
    pod = {"containers": [{"name": "c", "envFrom": [{"secretRef": {"name": "all"}}]}]}
    p = plan(deployments=[deployment(pod=pod)])

    assert not by_kind(p, "Secret") and "yourself" in p.missing[0].why
    assert "Secret" not in {m["kind"] for m in p.manifests}


def test_a_referenced_configmap_is_copied_when_the_source_has_it_and_listed_when_it_does_not():
    pod = {"containers": [{"name": "c", "envFrom": [{"configMapRef": {"name": "settings"}}]}]}
    cm = {"metadata": {"name": "settings", "namespace": SOURCE, "uid": "c-1"}, "data": {"A": "1"}}

    have = plan(deployments=[deployment(pod=pod)], configmaps=[cm])
    lack = plan(deployments=[deployment(pod=pod)], configmaps=[])

    assert by_kind(have, "ConfigMap")[0]["data"] == {"A": "1"} and have.deployable
    assert [m.kind for m in lack.missing] == ["ConfigMap"] and not lack.deployable


def test_an_external_name_service_is_not_assumed_reachable():
    p = plan(services=[service(type="ExternalName", externalName="db.example.com")])

    assert [m.kind for m in p.missing] == ["ExternalName"] and not p.deployable


def test_the_same_missing_reference_from_several_deployments_is_listed_once_per_user_not_per_pod():
    pod = {"containers": [{"name": "c", "envFrom": [{"secretRef": {"name": "all"}}, {"secretRef": {"name": "all"}}]}]}

    assert len(plan(deployments=[deployment(pod=pod)]).missing) == 1


@pytest.mark.parametrize("namespace", ["", "default", "kube-system", "kube-public", "kube-node-lease"])
def test_the_copy_never_goes_into_a_system_namespace_or_default(namespace):
    with pytest.raises(PreparationRefused) as refused:
        plan(target_namespace=namespace)
    assert refused.value.code == "TARGET_NAMESPACE_NOT_ALLOWED"


def test_the_digest_changes_with_what_is_deployed_and_with_what_is_missing():
    base = plan().digest

    assert plan().digest == base
    assert plan(deployments=[deployment(replicas=4)]).digest != base
    assert plan(deployments=[deployment(pod={"containers": [{"name": "c", "envFrom": [{"secretRef": {"name": "x"}}]}]})]).digest != base


# --- the manual fallback ---------------------------------------------------------------------------------------------------------

def text(*objects):
    return yaml.safe_dump_all([{"apiVersion": "v1", **o} for o in objects])


def test_pasted_manifests_are_forced_into_the_test_namespace_and_previewed_like_any_other():
    p = parse_manual(text({"kind": "Service", "metadata": {"name": "web", "namespace": "elsewhere"}, "spec": {"ports": [{"port": 80}]}}), TARGET)

    assert p.manifests[0]["metadata"]["namespace"] == TARGET and p.source_namespace == "(pasted)" and p.deployable


@pytest.mark.parametrize("kind", ["Namespace", "ClusterRole", "ClusterRoleBinding", "PersistentVolume", "Pod", "CronJob", "Job", "NetworkPolicy"])
def test_pasted_kinds_outside_the_allowed_four_are_refused(kind):
    with pytest.raises(PreparationRefused) as refused:
        parse_manual(text({"kind": kind, "metadata": {"name": "x"}}), TARGET)
    assert refused.value.code == "KIND_NOT_ALLOWED"


def test_a_pasted_secret_is_refused_with_the_instruction_to_create_it_yourself():
    with pytest.raises(PreparationRefused) as refused:
        parse_manual(text({"kind": "Deployment", "metadata": {"name": "d"}, "spec": {}}, {"kind": "Secret", "metadata": {"name": "s"}, "stringData": {"p": "x"}}), TARGET)

    assert refused.value.code == "SECRET_REFUSED" and "yourself" in refused.value.message


@pytest.mark.parametrize("bad", ["", "   ", "{not: [valid", "- just\n- a list\n", "kind: Service\n"])
def test_pasted_text_that_is_not_manifests_is_refused(bad):
    with pytest.raises(PreparationRefused):
        parse_manual(bad, TARGET)


def test_a_pasted_configmap_nobody_refers_to_is_still_deployed():
    p = parse_manual(text({"kind": "ConfigMap", "metadata": {"name": "cfg"}, "data": {"a": "b"}}), TARGET)

    assert [m["kind"] for m in p.manifests] == ["ConfigMap"]


# --- declared intent on a target ---------------------------------------------------------------------------------------------------

def test_the_fallback_attaches_declared_intent_from_the_targets_own_deployments_and_says_where_it_came_from():
    graph = bookinfo_scenario("concentrated").deployment.model_copy(deep=True)
    for service_ in graph.services:
        service_.declared_intent = None
    items = [{"metadata": {"name": s.name, "namespace": "bookinfo"}, "spec": {"replicas": s.declared_replicas, "selector": {"matchLabels": {"app": s.name}},
             "template": {"metadata": {"labels": {"app": s.name}}, "spec": {"containers": [{"name": "c", "resources": {"requests": {"cpu": "100m", "memory": "64Mi"}}}]}}}}
            for s in graph.services]

    enriched, report = attach_declared_intent(graph, items, [{"metadata": {"name": "w1", "labels": {"kubernetes.io/hostname": "w1"}}}])

    assert report["origin"] == ORIGIN and sorted(report["servicesWithIntent"]) == sorted(s.name for s in graph.services)
    reviews = next(s for s in enriched.services if s.name == "reviews")
    assert reviews.declared_intent.declared_replicas == 3 and reviews.declared_intent.selector == {"app": "reviews"}
    assert next(n for n in enriched.nodes if n.name == "w1").labels  # labels read where the graph had none
    assert all(s.declared_intent is None for s in graph.services), "the input graph is not changed"
    assert as_analysis(enriched).data_source.endswith(f"({ORIGIN})") and as_analysis(enriched).graph is enriched


def test_a_service_with_no_single_backing_deployment_gets_no_intent_never_a_guess():
    graph = bookinfo_scenario("concentrated").deployment.model_copy(deep=True)
    for service_ in graph.services:
        service_.declared_intent = None
    reviews = next(s for s in graph.services if s.name == "reviews")
    reviews.deployment_names = ["reviews-v1", "reviews-v2"]
    items = [{"metadata": {"name": n, "namespace": "bookinfo"}, "spec": {"replicas": 1, "selector": {"matchLabels": {}}, "template": {"spec": {"containers": []}}}}
             for n in ("reviews-v1", "reviews-v2", "ratings")]

    enriched, report = attach_declared_intent(graph, items)

    assert "reviews" in report["servicesWithoutIntent"] and next(s for s in enriched.services if s.name == "reviews").declared_intent is None


# --- the memory assessment from what the proxy reports -----------------------------------------------------------------------------

GIB = 1024**3


def test_facts_from_the_proxy_feed_the_same_assessment_and_a_machine_that_cannot_hold_the_nodes_is_insufficient():
    facts = facts_from_engine({"memTotalBytes": int(3.8 * GIB), "ncpu": 5},
                              {"desktop-control-plane": int(0.8 * GIB), "desktop-worker": int(0.55 * GIB), "desktop-worker2": int(0.55 * GIB), "other": int(0.9 * GIB)})

    assert facts.kind_clusters == ("desktop",) and facts.host_memory_bytes is None and facts.architecture is None
    assessment = assess(facts, profile="minimal")
    assert assessment.verdict == "INSUFFICIENT" and assessment.host_note is None


def test_only_a_sufficient_verdict_allows_creation_and_uncertain_or_unknown_never_does():
    roomy = facts_from_engine({"memTotalBytes": 12 * GIB, "ncpu": 8}, {"desktop-control-plane": int(0.8 * GIB), "desktop-worker": int(0.55 * GIB)})

    assert assess(roomy, profile="minimal").verdict == "UNCERTAIN"
    assert assess(roomy, profile="minimal", app_memory_mib=300).verdict == "SUFFICIENT"
    assert assess(facts_from_engine({"memTotalBytes": 12 * GIB}, {}), profile="minimal").verdict == "UNKNOWN"
    assert assess(Facts(None, None, None, None), profile="minimal").verdict == "UNKNOWN"
