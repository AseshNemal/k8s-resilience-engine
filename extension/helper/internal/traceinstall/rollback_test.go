package traceinstall

import (
	"fmt"
	"strings"
	"testing"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/kube"
)

// applied returns a world in which Apply succeeded and nothing has been written since.
func applied(t *testing.T) *env {
	t.Helper()
	e := newEnv(t)
	if out := e.apply(); !out.OK {
		t.Fatalf("setup for the test failed: %+v", out)
	}
	e.sim.writes = nil
	e.sim.writeCount = 0
	return e
}

func istioWrites(w []string) []int {
	var at []int
	for i, line := range w {
		if strings.HasPrefix(line, "delete ") && (strings.Contains(line, "istio-system") || strings.Contains(line, "istio.io") ||
			strings.Contains(line, "ClusterRole") || strings.Contains(line, "WebhookConfiguration")) {
			at = append(at, i)
		}
	}
	return at
}

func TestUndoRestoresTheBenchmarkAndRemovesTheLabelBeforeAnythingOfIstio(t *testing.T) {
	e := applied(t)
	out := e.undo()
	if !out.OK || out.State != StateUndone {
		t.Fatalf("%+v", out)
	}
	e.assertRestored()
	w := e.sim.writes
	label := indexOf(w, "patch namespaces bookinfo")
	v2 := indexOf(w, "delete Deployment bookinfo/reviews-v2")
	vs := indexOf(w, "delete VirtualService bookinfo/trace-demo-reviews")
	if !(vs >= 0 && vs < v2 && v2 < label) {
		t.Fatalf("route and reviews-v2 must go before the label: %v", w)
	}
	istio := istioWrites(w)
	if len(istio) == 0 {
		t.Fatal("Istio was not removed")
	}
	if istio[0] < label {
		t.Fatalf("Istio object removed before the injection label (webhook fails closed): %v", w)
	}
	// every sidecar Pod was recreated before Istio went
	pods := e.sim.writesMatching("delete Pod bookinfo/")
	if len(pods) != 3 || pods[2] > istio[0] {
		t.Fatalf("the Pods must be back without sidecars before Istio is removed: %v", w)
	}
	// webhooks first, the namespace last
	if !strings.Contains(w[istio[0]], "WebhookConfiguration") || w[istio[len(istio)-1]] != "delete Namespace istio-system" {
		t.Fatalf("unexpected removal order: first %q last %q", w[istio[0]], w[istio[len(istio)-1]])
	}
	rec := e.record()
	if rec.Phase != PhaseRolledBack || len(rec.Remaining) != 0 {
		t.Fatalf("%+v", rec)
	}
	if again := e.undo(); !again.OK || again.State != StateNothingToUndo {
		t.Fatalf("%+v", again)
	}
}

func TestUndoShowsADialogAndChangesNothingWithoutApproval(t *testing.T) {
	for name, p := range map[string]*fakePrompter{"declined": {decision: approve.Declined}, "timed out": {decision: approve.TimedOut}, "no channel": {err: approve.ErrNoApprovalChannel}} {
		e := applied(t)
		e.deps.Prompter = p
		if out := e.undo(); out.OK {
			t.Errorf("%s: %+v", name, out)
		}
		if len(e.sim.writes) != 0 || e.record().Phase != PhaseApplied {
			t.Errorf("%s: changed %v, phase %s", name, e.sim.writes, e.record().Phase)
		}
		if len(p.requests) != 1 || !strings.Contains(p.requests[0].Body, "recorded as created") {
			t.Errorf("%s: %+v", name, p.requests)
		}
	}
}

func TestUndoWithoutARecordOrForAnotherClusterRemovesNothing(t *testing.T) {
	e := newEnv(t)
	if out := e.undo(); out.State != StateNoRecord || len(e.sim.writes) != 0 {
		t.Fatalf("%+v", out)
	}

	// A record exists for a cluster that had this context name; the cluster behind it is now a different one.
	e = applied(t)
	other := e.params
	other.Identity.KubeSystemUID = "uid-a-recreated-cluster"
	out := Undo(t.Context(), e.deps, other)
	if out.State != StateIdentityChanged || len(e.sim.writes) != 0 {
		t.Fatalf("%+v", out)
	}
	// ... and nothing was asked of the person
	if len(e.prompter.requests) != 1 { // the one from applied()
		t.Errorf("%d dialogs", len(e.prompter.requests))
	}
}

func TestUndoNeverDeletesAnObjectItDidNotCreate(t *testing.T) {
	cases := map[string]func(e *env){
		"replaced by someone else": func(e *env) {
			delete(e.sim.objs, key("telemetries", "bookinfo", "trace-demo"))
			e.sim.put(kube.Resource{Plural: "telemetries", Namespaced: true}, kube.Object{Name: "trace-demo", Namespace: "bookinfo", UID: "theirs"})
		},
		"same labels, different UID": func(e *env) {
			old, _ := e.sim.get(kube.Resource{Plural: "telemetries", Namespaced: true}, "bookinfo", "trace-demo")
			labels := old.obj.Labels
			delete(e.sim.objs, key("telemetries", "bookinfo", "trace-demo"))
			e.sim.put(kube.Resource{Plural: "telemetries", Namespaced: true}, kube.Object{Name: "trace-demo", Namespace: "bookinfo", UID: "theirs", Labels: labels})
		},
	}
	for name, setup := range cases {
		e := applied(t)
		setup(e)
		out := e.undo()
		kept, ok := e.sim.get(kube.Resource{Plural: "telemetries", Namespaced: true}, "bookinfo", "trace-demo")
		if !ok || kept.obj.UID != "theirs" {
			t.Errorf("%s: somebody else's Telemetry was deleted", name)
		}
		// Their Telemetry still uses the definition, so the definition is kept and reported.
		if out.State != StateRollbackIncomplet || !strings.Contains(out.Message, "telemetries.telemetry.istio.io was kept") {
			t.Errorf("%s: %+v", name, out)
		}
		if _, still := e.sim.get(kube.CustomResourceDefinitions, "", "telemetries.telemetry.istio.io"); !still {
			t.Errorf("%s: a CRD in use was deleted", name)
		}
	}
}

func TestUndoKeepsTheIstioNamespaceIfSomethingElseLivesInIt(t *testing.T) {
	e := applied(t)
	e.sim.put(kube.ConfigMaps, kube.Object{Name: "my-own-config", Namespace: "istio-system"})
	out := e.undo()
	if out.State != StateRollbackIncomplet || !strings.Contains(out.Message, "istio-system namespace was kept") {
		t.Fatalf("%+v", out)
	}
	if _, ok := e.sim.get(kube.Namespaces, "", "istio-system"); !ok {
		t.Fatal("a namespace holding someone's object was deleted")
	}
	if _, ok := e.sim.get(kube.ConfigMaps, "istio-system", "my-own-config"); !ok {
		t.Fatal("someone's ConfigMap was deleted")
	}
	if _, ok := e.sim.get(kube.Deployments, "istio-system", "istiod"); ok {
		t.Error("istiod, which this setup created, should have been removed")
	}
}

func TestUndoKeepsADefinitionThatOtherIstioResourcesUse(t *testing.T) {
	e := applied(t)
	vs := kube.Resource{Group: "networking.istio.io", Version: "v1", Plural: "virtualservices", Kind: "VirtualService", Namespaced: true}
	e.sim.put(vs, kube.Object{Name: "someone-elses", Namespace: "default"})
	out := e.undo()
	if out.State != StateRollbackIncomplet || !strings.Contains(out.Message, "virtualservices.networking.istio.io was kept") {
		t.Fatalf("%+v", out)
	}
	if _, ok := e.sim.get(vs, "default", "someone-elses"); !ok {
		t.Fatal("a VirtualService in another namespace was deleted")
	}
	if _, ok := e.sim.get(kube.CustomResourceDefinitions, "", "gateways.networking.istio.io"); ok {
		t.Error("an unused definition should have been removed")
	}
}

func TestUndoKeepsADefinitionWhoseInstancesCannotBeListed(t *testing.T) {
	e := applied(t)
	e.sim.failList["list:envoyfilters"] = fmt.Errorf("forbidden")
	out := e.undo()
	if out.State != StateRollbackIncomplet || !strings.Contains(out.Message, "envoyfilters.networking.istio.io was kept") {
		t.Fatalf("%+v", out)
	}
}

func TestIfAWorkloadCannotBeRestoredIstioIsLeftInPlace(t *testing.T) {
	e := applied(t)
	pod := e.sim.podsOwnedBy("productpage", "v1")[0]
	e.sim.failAlways["delete:Pod "+strings.TrimPrefix(pod, "pods/")] = fmt.Errorf("API down")
	out := e.undo()
	if out.State != StateRollbackIncomplet || !strings.Contains(out.Message, "Istio was left in place") {
		t.Fatalf("%+v", out)
	}
	if n := len(istioWrites(e.sim.writes)); n != 0 {
		t.Fatalf("Istio was touched while a sidecar was still running: %v", e.sim.writes)
	}
	if e.sim.stuckPods != 0 {
		t.Fatal("the namespace could not create Pods")
	}
	// the label is already off, so once the API recovers the second Undo completes
	delete(e.sim.failAlways, "delete:Pod "+strings.TrimPrefix(pod, "pods/"))
	if again := e.undo(); !again.OK {
		t.Fatalf("%+v", again)
	}
	e.assertRestored()
}

func TestIfTheLabelCannotBeRemovedIstioIsLeftInPlace(t *testing.T) {
	e := applied(t)
	e.sim.failAlways["patch:namespaces bookinfo"] = fmt.Errorf("API down")
	out := e.undo()
	if out.State != StateRollbackIncomplet || !strings.Contains(out.Message, "istio-injection label could not be removed") {
		t.Fatalf("%+v", out)
	}
	if len(istioWrites(e.sim.writes)) != 0 || e.sim.stuckPods != 0 {
		t.Fatalf("Istio must stay while the namespace is labelled: %v", e.sim.writes)
	}
}

func TestAnUndoThatIsKilledAtAnyWriteCanBeRunAgain(t *testing.T) {
	probe := applied(t)
	if out := probe.undo(); !out.OK {
		t.Fatal(out)
	}
	total := len(probe.sim.writes)
	for _, after := range []bool{false, true} {
		for n := 1; n <= total; n++ {
			after, n := after, n
			t.Run(fmt.Sprintf("kill at write %d after=%v", n, after), func(t *testing.T) {
				t.Parallel()
				e := applied(t)
				if after {
					e.sim.killAfter = n
				} else {
					e.sim.killBefore = n
				}
				func() {
					defer func() {
						if recover() == nil {
							t.Fatal("the simulated kill did not happen")
						}
					}()
					e.undo()
				}()
				e.sim.killBefore, e.sim.killAfter = 0, 0
				if out := e.undo(); !out.OK {
					t.Fatalf("%+v", out)
				}
				e.assertRestored()
			})
		}
	}
}

func TestTheRecordKeepsTheJournalOfWhatHappened(t *testing.T) {
	e := applied(t)
	e.undo()
	rec := e.record()
	var text []string
	for _, s := range rec.Steps {
		text = append(text, s.Step+" "+s.Status)
	}
	joined := strings.Join(text, "\n")
	for _, want := range []string{"install Istio 1.31.2 started", "install Istio 1.31.2 done", "memory check after the Istio install done", "restart ratings-v1-p3 done", "rollback done"} {
		if !strings.Contains(joined, want) {
			t.Errorf("the journal lacks %q:\n%s", want, joined)
		}
	}
}
