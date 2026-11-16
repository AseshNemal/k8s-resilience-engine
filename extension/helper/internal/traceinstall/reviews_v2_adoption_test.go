package traceinstall

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"resilience-engine/helper/internal/bookinfotrace"
	"resilience-engine/helper/internal/kube"
)

// adoptReviewsV2 creates the connected cluster's layout: no reviews-v1 and a user-owned three-replica reviews-v2.
func adoptReviewsV2(t *testing.T, e *env, replicas int) {
	t.Helper()
	delete(e.sim.objs, key("deployments", "bookinfo", "reviews-v1"))
	for _, podKey := range e.sim.podsOwnedBy("reviews", "v1") {
		delete(e.sim.objs, podKey)
	}
	ready := true
	e.sim.put(kube.Deployments, kube.Object{Name: "reviews-v2", Namespace: "bookinfo", UID: "uid-user-reviews-v2",
		Replicas: &replicas, ReadyReplicas: &replicas, Containers: []string{"reviews"}, SpecDigest: "digest-user-v2"})
	for n := 0; n < replicas; n++ {
		e.sim.put(kube.Pods, kube.Object{Name: fmt.Sprintf("reviews-v2-pre-%d", n), Namespace: "bookinfo", UID: fmt.Sprintf("uid-user-v2-pod-%d", n),
			Labels: map[string]string{"app": "reviews", "version": "v2"}, Containers: []string{"reviews"}, Ready: &ready, Phase: "Running"})
	}
	e.sim.baseKeys = map[string]bool{}
	for objectKey := range e.sim.objs {
		e.sim.baseKeys[objectKey] = true
	}
	facts, err := bookinfotrace.Gather(context.Background(), e.sim)
	if err != nil {
		t.Fatal(err)
	}
	e.base = bookinfotrace.TakeSnapshot(facts, "docker-desktop", "base")
}

func assertUserReviewsV2Untouched(t *testing.T, e *env, replicas int) {
	t.Helper()
	deployment, ok := e.sim.get(kube.Deployments, "bookinfo", "reviews-v2")
	if !ok || deployment.obj.UID != "uid-user-reviews-v2" || deployment.obj.SpecDigest != "digest-user-v2" ||
		deployment.obj.Replicas == nil || *deployment.obj.Replicas != replicas {
		t.Fatalf("the user's Deployment was changed or recreated: %+v", deployment)
	}
	for _, write := range e.sim.writes {
		if write == "create Deployment bookinfo/reviews-v2" || write == "delete Deployment bookinfo/reviews-v2" {
			t.Fatalf("the user's Deployment was written: %s", write)
		}
	}
}

func TestApplyAdoptsExistingThreeReplicaReviewsV2WithoutOwningItsDeployment(t *testing.T) {
	e := newEnv(t)
	adoptReviewsV2(t, e, 3)
	out := e.apply()
	if !out.OK || out.State != StateApplied {
		t.Fatalf("%+v", out)
	}
	assertUserReviewsV2Untouched(t, e, 3)
	rec := e.record()
	if rec.Entry(reviewsV2Ref()) != nil || len(rec.PlannedPods) != 6 {
		t.Fatalf("the adoption was not recorded safely: %+v", rec)
	}
	pods, err := (&run{d: e.deps}).podsOf(context.Background(), "reviews", "v2")
	if err != nil || len(pods) != 3 {
		t.Fatalf("reviews-v2 Pods: %v %v", pods, err)
	}
	for _, pod := range pods {
		if !hasSidecar(pod) {
			t.Errorf("%s has no sidecar", pod.Name)
		}
	}
	body := e.prompter.requests[0].Body
	if !strings.Contains(body, "keep Deployment reviews-v2") || !strings.Contains(body, "reviews-v2-pre-2") || strings.Contains(body, "create Deployment reviews-v2") {
		t.Fatalf("the approval does not describe adoption exactly:\n%s", body)
	}
	if out := e.verify(); !out.OK {
		t.Fatalf("the adopted layout does not verify: %+v", out)
	}
}

func TestFailedGateRollsBackAdoptedReviewsV2PodsButNeverDeletesItsDeployment(t *testing.T) {
	e := newEnv(t)
	adoptReviewsV2(t, e, 3)
	e.deps.Measure = func(context.Context) bookinfotrace.Measurements {
		m := e.meas
		if len(e.sim.writes) > 0 {
			m.SidecarMaxMiB = f(140)
		}
		return m
	}
	if out := e.apply(); out.State != StateApplyFailed {
		t.Fatalf("%+v", out)
	}
	e.assertRestored()
	assertUserReviewsV2Untouched(t, e, 3)
}

func TestUndoRestoresAdoptedReviewsV2PodsAndPreservesDeployment(t *testing.T) {
	e := newEnv(t)
	adoptReviewsV2(t, e, 3)
	if out := e.apply(); !out.OK {
		t.Fatalf("%+v", out)
	}
	e.sim.writes = nil
	out := e.undo()
	if !out.OK || out.State != StateUndone {
		t.Fatalf("%+v", out)
	}
	e.assertRestored()
	assertUserReviewsV2Untouched(t, e, 3)
	if len(e.sim.writesMatching("delete Pod bookinfo/reviews-v2-")) != 3 {
		t.Fatalf("all three adopted Pods must be restored without sidecars: %v", e.sim.writes)
	}
	if strings.Contains(e.prompter.requests[1].Body, "delete Deployment reviews-v2") {
		t.Fatal("Undo offered to delete a Deployment the run did not create")
	}
}
