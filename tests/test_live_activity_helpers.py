"""The pure readers behind the Apply-and-test live view: which pods run the Deployment's current template, and what a pod is doing."""
from app.integrations.kubernetes.validation_gateway import _current_template_hash, _pod_activity


def deployment(revision="3"):
    return {"metadata": {"name": "db", "annotations": {"deployment.kubernetes.io/revision": revision}}}


def replicaset(owner, revision, hash_):
    return {"metadata": {"name": f"{owner}-{hash_}", "labels": {"pod-template-hash": hash_},
                         "annotations": {"deployment.kubernetes.io/revision": revision},
                         "ownerReferences": [{"kind": "Deployment", "name": owner}]}}


def pod(hash_, *, ready=True, waiting=None, deleting=False, node="worker-a", unschedulable=False):
    conditions = [{"type": "Ready", "status": "True" if ready else "False"}]
    if unschedulable:
        conditions.append({"type": "PodScheduled", "status": "False", "reason": "Unschedulable"})
    status = {"phase": "Pending" if unschedulable else "Running", "conditions": conditions}
    if waiting:
        status["containerStatuses"] = [{"restartCount": 4, "state": {"waiting": {"reason": waiting}}}]
    meta = {"name": f"db-{hash_}", "labels": {"pod-template-hash": hash_}}
    if deleting:
        meta["deletionTimestamp"] = "2026-10-25T00:00:00Z"
    return {"metadata": meta, "spec": {"nodeName": None if unschedulable else node}, "status": status}


def test_the_current_template_is_the_replicaset_with_the_deployments_revision():
    sets = [replicaset("db", "2", "old111"), replicaset("db", "3", "new222"), replicaset("other", "3", "zzz999")]
    assert _current_template_hash(deployment("3"), sets) == "new222"
    assert _current_template_hash(deployment("9"), sets) is None                       # unknown, never guessed
    assert _current_template_hash({"metadata": {"name": "db"}}, sets) is None


def test_a_pod_says_whether_it_runs_the_new_or_an_old_template():
    assert _pod_activity(pod("new222"), "new222")["version"] == "new"
    assert _pod_activity(pod("old111"), "new222")["version"] == "old"
    assert _pod_activity(pod("old111"), None)["version"] is None


def test_a_pod_state_is_what_a_person_would_say():
    assert _pod_activity(pod("a"))["state"] == "Ready"
    assert _pod_activity(pod("a", ready=False))["state"] == "Starting"
    assert _pod_activity(pod("a", ready=False, waiting="CrashLoopBackOff"))["state"] == "CrashLoopBackOff"
    assert _pod_activity(pod("a", ready=False, waiting="CrashLoopBackOff"))["restarts"] == 4
    assert _pod_activity(pod("a", deleting=True))["state"] == "Terminating"
    unplaced = _pod_activity(pod("a", ready=False, unschedulable=True))
    assert unplaced["state"] == "Unschedulable" and unplaced["node"] is None
