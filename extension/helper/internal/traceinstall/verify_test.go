package traceinstall

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/bookinfotrace"
	"resilience-engine/helper/internal/kube"
)

func (e *env) verify() Outcome  { return Verify(context.Background(), e.deps, e.params) }
func (e *env) traffic() Outcome { return RunTraffic(context.Background(), e.deps, e.params) }

// ---- Verify is read-only ----

func TestVerifyIsReadOnlyAsksNothingAndCreatesNoPod(t *testing.T) {
	e := applied(t)
	asked := len(e.prompter.requests)
	out := e.verify()
	if !out.OK || out.State != StateChecksPassed || out.Verify == nil || out.Verify.RequestsSent {
		t.Fatalf("%+v", out)
	}
	if len(e.sim.writes) != 0 {
		t.Fatalf("Verify wrote to the cluster: %v", e.sim.writes)
	}
	if len(e.prompter.requests) != asked {
		t.Fatal("Verify asked for an approval; it must not need one")
	}
	if _, there := e.sim.get(kube.Pods, "bookinfo", "trace-demo-client"); there {
		t.Fatal("Verify created the client Pod")
	}
	got := out.Verify
	if got.AppliedAddress != "192.168.65.254" || got.CurrentAddress != got.AppliedAddress || got.AddressDrift || got.Readings == "" {
		t.Fatalf("%+v", got)
	}
	var workloads []string
	for _, p := range got.Instrumented {
		if !p.Sidecar || !p.Ready {
			t.Errorf("%+v", p)
		}
		workloads = append(workloads, p.Workload)
	}
	if strings.Join(workloads, ",") != "ratings-v1,details-v1,productpage-v1,reviews-v2" {
		t.Fatalf("%v", workloads)
	}
	if e.record().Phase != PhaseApplied {
		t.Fatal("Verify must not change the phase")
	}
}

func TestVerifyDoesNotWriteEvenToTheLocalRecord(t *testing.T) {
	e := applied(t)
	before := e.record().UpdatedAt
	e.clock.now = e.clock.now.Add(time.Hour)
	e.verify()
	if after := e.record().UpdatedAt; after != before {
		t.Fatalf("Verify changed the record: %s -> %s", before, after)
	}
}

func TestVerifyNeedsAnAppliedSetup(t *testing.T) {
	e := newEnv(t)
	if out := e.verify(); out.State != StateNotApplied || len(e.sim.writes) != 0 {
		t.Fatalf("%+v", out)
	}
	e = applied(t)
	e.undo()
	e.sim.writes = nil
	for name, out := range map[string]Outcome{"verify": e.verify(), "traffic": e.traffic()} {
		if out.State != StateNotApplied || len(e.sim.writes) != 0 {
			t.Fatalf("%s: %+v", name, out)
		}
	}
}

func TestVerifyReportsADriftedJaegerAddress(t *testing.T) {
	e := applied(t)
	e.address.IPv4 = "192.168.65.1"
	asked := len(e.prompter.requests)
	for name, out := range map[string]Outcome{"verify": e.verify(), "traffic": e.traffic()} {
		if out.State != StateVerifyFailed || out.Verify == nil || !out.Verify.AddressDrift || !strings.Contains(out.Message, "192.168.65.1") {
			t.Fatalf("%s: %+v", name, out)
		}
	}
	if len(e.sim.writes) != 0 || len(e.prompter.requests) != asked {
		t.Fatalf("nothing may be asked or written when the setup is not healthy: %v", e.sim.writes)
	}
}

func TestAPodWithoutASidecarOrNotReadyFailsBothAndAsksNothing(t *testing.T) {
	for name, change := range map[string]func(o *simObject){
		"no sidecar": func(o *simObject) { o.obj.Containers = []string{"productpage"} },
		"not ready":  func(o *simObject) { notReady := false; o.obj.Ready = &notReady },
	} {
		e := applied(t)
		change(e.sim.objs[e.sim.podsOwnedBy("productpage", "v1")[0]])
		asked := len(e.prompter.requests)
		for op, out := range map[string]Outcome{"verify": e.verify(), "traffic": e.traffic()} {
			if out.State != StateVerifyFailed || !strings.Contains(out.Message, "no working sidecar") {
				t.Errorf("%s %s: %+v", name, op, out)
			}
		}
		if len(e.sim.writes) != 0 || len(e.prompter.requests) != asked {
			t.Errorf("%s: %v", name, e.sim.writes)
		}
	}
}

func TestVerifyNoticesAMissingPieceOfTheSetup(t *testing.T) {
	e := applied(t)
	delete(e.sim.objs, key("virtualservices", "bookinfo", "trace-demo-reviews"))
	if out := e.verify(); out.State != StateVerifyFailed || len(e.sim.writes) != 0 {
		t.Fatalf("%+v", out)
	}
}

// ---- Run test traffic has its own approval ----

func TestTestTrafficAsksFirstAndShowsExactlyWhatItCreates(t *testing.T) {
	e := applied(t)
	asked := len(e.prompter.requests)
	out := e.traffic()
	if !out.OK || out.State != StateTrafficSent || out.Verify == nil || !out.Verify.RequestsSent {
		t.Fatalf("%+v", out)
	}
	if len(e.prompter.requests) != asked+1 {
		t.Fatalf("%d approvals for the traffic", len(e.prompter.requests)-asked)
	}
	body := e.prompter.requests[asked].Body
	for _, want := range []string{"docker-desktop", "CREATES one temporary Pod", "trace-demo-client", bookinfotrace.TrafficImage, "60 real requests",
		"creates no trace of its own", "never contacts Jaeger", "deleted when it finishes", "No existing Deployment", "read-only Kubernetes access is not changed"} {
		if !strings.Contains(body, want) {
			t.Errorf("the dialog does not say %q:\n%s", want, body)
		}
	}
	if w := e.sim.writes; len(w) != 2 || w[0] != "create Pod bookinfo/trace-demo-client" || w[1] != "delete Pod bookinfo/trace-demo-client" {
		t.Fatalf("the traffic operation may only create and delete its client Pod: %v", w)
	}
	if _, there := e.sim.get(kube.Pods, "bookinfo", "trace-demo-client"); there {
		t.Fatal("the client Pod was left behind")
	}
	if e.record().Phase != PhaseApplied {
		t.Fatal("the phase must not change")
	}
	e.sim.writes = nil
	if again := e.traffic(); !again.OK { // repeatable
		t.Fatalf("%+v", again)
	}
}

func TestWithoutApprovalNoPodIsCreated(t *testing.T) {
	for name, p := range map[string]*fakePrompter{
		"declined": {decision: approve.Declined}, "timed out": {decision: approve.TimedOut}, "no channel": {err: approve.ErrNoApprovalChannel},
	} {
		e := applied(t)
		e.deps.Prompter = p
		out := e.traffic()
		if out.OK || len(p.requests) != 1 {
			t.Errorf("%s: %+v (%d dialogs)", name, out, len(p.requests))
		}
		if len(e.sim.writes) != 0 {
			t.Errorf("%s: the cluster was written to: %v", name, e.sim.writes)
		}
		if _, there := e.sim.get(kube.Pods, "bookinfo", "trace-demo-client"); there {
			t.Errorf("%s: a Pod was created without approval", name)
		}
	}
}

func TestTheDialogAndTheManifestAgreeOnTheImageTheNameAndTheCount(t *testing.T) {
	doc, err := documentOf(fileClient, "Pod")
	if err != nil {
		t.Fatal(err)
	}
	meta := doc["metadata"].(map[string]any)
	spec := doc["spec"].(map[string]any)
	container := spec["containers"].([]any)[0].(map[string]any)
	if meta["name"] != bookinfotrace.TrafficPodName || container["image"] != bookinfotrace.TrafficImage {
		t.Fatalf("the dialog describes %s (%s), the manifest creates %v (%v)", bookinfotrace.TrafficPodName, bookinfotrace.TrafficImage, meta["name"], container["image"])
	}
	env := container["env"].([]any)[0].(map[string]any)
	if env["name"] != "REQUESTS" || env["value"] != fmt.Sprint(bookinfotrace.TrafficRequests) {
		t.Fatalf("the dialog says %d requests, the manifest sends %v", bookinfotrace.TrafficRequests, env)
	}
	limit := container["resources"].(map[string]any)["limits"].(map[string]any)["memory"]
	if limit != "32Mi" {
		t.Fatalf("the dialog says 32 MiB, the manifest limits %v", limit)
	}
}

func TestTrafficIsNotStartedIfTheSetupChangesWhileThePersonDecides(t *testing.T) {
	e := applied(t)
	e.prompter.hook = func() { // a sidecar disappears while the dialog is open
		e.sim.objs[e.sim.podsOwnedBy("details", "v1")[0]].obj.Containers = []string{"details"}
	}
	out := e.traffic()
	if out.OK || !strings.Contains(out.Message, "changed while you were deciding") {
		t.Fatalf("%+v", out)
	}
	if len(e.sim.writes) != 0 {
		t.Fatalf("%v", e.sim.writes)
	}
}

func TestTrafficDoesNotAskWhenMemoryIsTight(t *testing.T) {
	e := applied(t)
	e.meas.VMUsedMiB = f(3300)
	asked := len(e.prompter.requests)
	if out := e.traffic(); out.State != StateVerifyFailed || len(e.sim.writes) != 0 || len(e.prompter.requests) != asked {
		t.Fatalf("%+v", out)
	}
	e = applied(t)
	e.meas.SwapUsedMiB = nil
	asked = len(e.prompter.requests)
	if out := e.traffic(); out.State != StateVerifyFailed || len(e.prompter.requests) != asked {
		t.Fatalf("an unreadable swap figure is not 'fine': %+v", out)
	}
	// Verify only reports memory; it does not refuse because of it
	e = applied(t)
	e.meas.VMUsedMiB = f(3300)
	if out := e.verify(); !out.OK || !strings.Contains(out.Verify.Readings, "3300") {
		t.Fatalf("%+v", out)
	}
}

func TestAClientPodThatNeverFinishesIsReportedAndStillRemoved(t *testing.T) {
	e := applied(t)
	e.sim.clientDone = 24 * time.Hour
	out := e.traffic()
	if out.State != StateVerifyFailed || out.Verify.RequestsSent || !strings.Contains(out.Message, "did not finish") {
		t.Fatalf("%+v", out)
	}
	if _, still := e.sim.get(kube.Pods, "bookinfo", "trace-demo-client"); still {
		t.Fatal("the client Pod was left behind")
	}
}

func TestACancelledTrafficRunStillRemovesItsClientPod(t *testing.T) {
	e := applied(t)
	ctx, cancel := context.WithCancel(context.Background())
	start := e.clock.sleeps
	e.clock.onSleep = func(n int) {
		if n == start+3 {
			cancel()
		}
	}
	out := RunTraffic(ctx, e.deps, e.params)
	cancel()
	if out.OK {
		t.Fatalf("%+v", out)
	}
	if _, still := e.sim.get(kube.Pods, "bookinfo", "trace-demo-client"); still {
		t.Fatal("the client Pod was left behind")
	}
}

func TestAClientPodThatCannotBeCreatedIsAFailureNotAPass(t *testing.T) {
	e := applied(t)
	e.sim.failAlways["create:Pod bookinfo/trace-demo-client"] = fmt.Errorf("quota exceeded")
	out := e.traffic()
	if out.State != StateVerifyFailed || out.Verify.RequestsSent || strings.Contains(out.Message, "quota") {
		t.Fatalf("%+v", out)
	}
}

func TestATrafficRunKilledAtAnyWriteLeavesNothingBehindAfterUndo(t *testing.T) {
	for _, after := range []bool{false, true} {
		for n := 1; n <= 2; n++ {
			e := applied(t)
			if after {
				e.sim.killAfter = n
			} else {
				e.sim.killBefore = n
			}
			func() {
				defer func() { recover() }()
				e.traffic()
			}()
			e.sim.killBefore, e.sim.killAfter = 0, 0
			if out := e.undo(); !out.OK {
				t.Fatalf("kill at %d after=%v: %+v", n, after, out)
			}
			e.assertRestored()
		}
	}
}
