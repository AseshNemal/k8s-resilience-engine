package bookinfotrace

import (
	"strings"
	"testing"
)

func diffText(d []Diff) string {
	var parts []string
	for _, x := range d {
		parts = append(parts, x.What+": "+x.Before+" -> "+x.After)
	}
	return strings.Join(parts, "; ")
}

func TestASnapshotCapturesWhatARollbackMustRestore(t *testing.T) {
	s := TakeSnapshot(benchmarkFacts(), "docker-desktop", "2026-10-22T12:00:00Z")
	if s.Context != "docker-desktop" || s.NamespaceLabels["kubernetes.io/metadata.name"] != "bookinfo" || len(s.Deployments) != 4 || s.ReviewsV2Preexisting {
		t.Fatalf("%+v", s)
	}
	if got := s.PodContainers["productpage/v1"]; len(got) != 1 || got[0] != "productpage" {
		t.Fatalf("%v", s.PodContainers)
	}
	facts := benchmarkFacts()
	facts.Deployments["reviews-v2"] = DeploymentFact{}
	if !TakeSnapshot(facts, "x", "t").ReviewsV2Preexisting {
		t.Fatal("a reviews-v2 that was already there must be recorded, so Undo does not remove it")
	}
}

func TestARestoredClusterHasNoDifferences(t *testing.T) {
	before := TakeSnapshot(benchmarkFacts(), "x", "t1")
	// After Undo the Pods have new random names, which the snapshot deliberately does not key on.
	restored := benchmarkFacts()
	for n := range restored.Pods {
		restored.Pods[n].Name += "-renamed"
	}
	if d := Compare(before, TakeSnapshot(restored, "x", "t2")); len(d) != 0 {
		t.Fatalf("unexpected differences: %s", diffText(d))
	}
}

func TestEverythingTheSetupChangedIsFoundIfItIsLeftBehind(t *testing.T) {
	before := TakeSnapshot(benchmarkFacts(), "x", "t1")
	left := benchmarkFacts()
	left.NamespaceLabels["istio-injection"] = "enabled"              // the label
	left.Deployments["reviews-v2"] = DeploymentFact{Replicas: i(1)}  // the Deployment the setup created
	left.Pods[0].Containers = []string{"productpage", "istio-proxy"} // a sidecar that is still there
	left.Pods = append(left.Pods, PodFact{Name: "reviews-v2-x", App: "reviews", Version: "v2", Containers: []string{"reviews", "istio-proxy"}})
	text := diffText(Compare(before, TakeSnapshot(left, "x", "t2")))
	for _, want := range []string{"namespace label istio-injection", "Deployment reviews-v2: absent -> present", "Pod productpage/v1 containers", "Pod reviews/v2: absent -> running"} {
		if !strings.Contains(text, want) {
			t.Errorf("missing %q in: %s", want, text)
		}
	}
}

func TestADeploymentSpecThatChangedIsFoundByItsDigest(t *testing.T) {
	// `kubectl rollout restart` would add an annotation to the Pod template, which changes the digest.
	before := TakeSnapshot(benchmarkFacts(), "x", "t1")
	changed := benchmarkFacts()
	d := changed.Deployments["details-v1"]
	d.SpecDigest = "ffff9999ffff9999"
	changed.Deployments["details-v1"] = d
	text := diffText(Compare(before, TakeSnapshot(changed, "x", "t2")))
	if !strings.Contains(text, "Deployment details-v1 spec: bbbb2222bbbb -> ffff9999ffff") {
		t.Fatalf("%s", text)
	}
}

func TestChangedReplicasAndDeletedWorkloadsAreFound(t *testing.T) {
	before := TakeSnapshot(benchmarkFacts(), "x", "t1")
	after := benchmarkFacts()
	r := after.Deployments["ratings-v1"]
	r.Replicas = i(2)
	after.Deployments["ratings-v1"] = r
	delete(after.Deployments, "reviews-v1")
	after.Pods = after.Pods[:3]
	text := diffText(Compare(before, TakeSnapshot(after, "x", "t2")))
	for _, want := range []string{"ratings-v1 replicas: 1 -> 2", "Deployment reviews-v1: present -> missing", "Pod reviews/v1: running -> missing"} {
		if !strings.Contains(text, want) {
			t.Errorf("missing %q in: %s", want, text)
		}
	}
}

func TestUnknownReplicasAreComparedAsUnknownNotZero(t *testing.T) {
	a := benchmarkFacts()
	d := a.Deployments["details-v1"]
	d.Replicas = nil
	a.Deployments["details-v1"] = d
	if diffs := Compare(TakeSnapshot(a, "x", "t"), TakeSnapshot(a, "x", "t")); len(diffs) != 0 {
		t.Fatalf("%s", diffText(diffs))
	}
	if diffs := Compare(TakeSnapshot(benchmarkFacts(), "x", "t"), TakeSnapshot(a, "x", "t")); len(diffs) == 0 {
		t.Fatal("known versus unknown replicas is a difference")
	}
}
