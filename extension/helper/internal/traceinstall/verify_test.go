package traceinstall

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/bookinfotrace"
	"resilience-engine/helper/internal/kube"
)

func (e *env) verify() Outcome { return Verify(context.Background(), e.deps, e.params) }

func TestVerifyRunsRealRequestsFromAnInstrumentedPodAndRemovesIt(t *testing.T) {
	e := applied(t)
	out := e.verify()
	if !out.OK || out.State != StateTrafficSent || out.Verify == nil || !out.Verify.RequestsSent {
		t.Fatalf("%+v", out)
	}
	if got := out.Verify; got.AppliedAddress != "192.168.65.254" || got.CurrentAddress != got.AppliedAddress || got.AddressDrift {
		t.Fatalf("%+v", got)
	}
	var workloads []string
	for _, p := range out.Verify.Instrumented {
		if !p.Sidecar || !p.Ready {
			t.Errorf("%+v", p)
		}
		workloads = append(workloads, p.Workload)
	}
	if strings.Join(workloads, ",") != "ratings-v1,details-v1,productpage-v1,reviews-v2" {
		t.Fatalf("%v", workloads)
	}
	if w := e.sim.writes; len(w) != 2 || w[0] != "create Pod bookinfo/trace-demo-client" || w[1] != "delete Pod bookinfo/trace-demo-client" {
		t.Fatalf("Verify may only create and delete its client Pod: %v", w)
	}
	if left := e.sim.leftovers(); len(left) != len(applied2Leftovers(e)) {
		t.Fatalf("the client Pod was left behind: %v", left)
	}
	if e.record().Phase != PhaseApplied {
		t.Fatal("Verify must not change the phase")
	}
	// repeatable
	e.sim.writes = nil
	if again := e.verify(); !again.OK {
		t.Fatalf("%+v", again)
	}
}

// applied2Leftovers is what an applied setup legitimately leaves in the cluster (everything Apply created).
func applied2Leftovers(e *env) []string {
	n := 0
	for _, entry := range e.record().Inventory {
		if entry.State == EntryCreated {
			n++
		}
	}
	return make([]string, n)
}

func TestVerifyNeedsAnAppliedSetup(t *testing.T) {
	e := newEnv(t)
	if out := e.verify(); out.State != StateNotApplied || len(e.sim.writes) != 0 {
		t.Fatalf("%+v", out)
	}
	e = applied(t)
	e.undo()
	e.sim.writes = nil
	if out := e.verify(); out.State != StateNotApplied || len(e.sim.writes) != 0 {
		t.Fatalf("%+v", out)
	}
}

func TestVerifyReportsADriftedJaegerAddressAndSendsNothing(t *testing.T) {
	e := applied(t)
	e.address.IPv4 = "192.168.65.1"
	out := e.verify()
	if out.State != StateVerifyFailed || out.Verify == nil || !out.Verify.AddressDrift || !strings.Contains(out.Message, "192.168.65.1") {
		t.Fatalf("%+v", out)
	}
	if len(e.sim.writes) != 0 {
		t.Fatalf("%v", e.sim.writes)
	}
}

func TestVerifyRefusesAPodWithoutASidecarOrNotReady(t *testing.T) {
	for name, change := range map[string]func(o *simObject){
		"no sidecar": func(o *simObject) { o.obj.Containers = []string{"productpage"} },
		"not ready":  func(o *simObject) { notReady := false; o.obj.Ready = &notReady },
	} {
		e := applied(t)
		change(e.sim.objs[e.sim.podsOwnedBy("productpage", "v1")[0]])
		out := e.verify()
		if out.State != StateVerifyFailed || !strings.Contains(out.Message, "no working sidecar") || len(e.sim.writes) != 0 {
			t.Errorf("%s: %+v %v", name, out, e.sim.writes)
		}
	}
}

func TestVerifyDoesNotStartWhenMemoryIsTight(t *testing.T) {
	e := applied(t)
	e.meas.VMUsedMiB = f(3300)
	if out := e.verify(); out.State != StateVerifyFailed || len(e.sim.writes) != 0 {
		t.Fatalf("%+v", out)
	}
	e = applied(t)
	e.meas.SwapUsedMiB = nil
	if out := e.verify(); out.State != StateVerifyFailed || len(e.sim.writes) != 0 {
		t.Fatalf("an unreadable swap figure is not 'fine': %+v", out)
	}
}

func TestAClientPodThatNeverFinishesIsReportedAndStillRemoved(t *testing.T) {
	e := applied(t)
	e.sim.clientDone = 24 * time.Hour
	out := e.verify()
	if out.State != StateVerifyFailed || out.Verify.RequestsSent || !strings.Contains(out.Message, "did not finish") {
		t.Fatalf("%+v", out)
	}
	if _, still := e.sim.get(kube.Pods, "bookinfo", "trace-demo-client"); still {
		t.Fatal("the client Pod was left behind")
	}
}

func TestACancelledVerifyStillRemovesItsClientPod(t *testing.T) {
	e := applied(t)
	ctx, cancel := context.WithCancel(context.Background())
	start := e.clock.sleeps
	e.clock.onSleep = func(n int) {
		if n == start+3 {
			cancel()
		}
	}
	out := Verify(ctx, e.deps, e.params)
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
	out := e.verify()
	if out.State != StateVerifyFailed || out.Verify.RequestsSent {
		t.Fatalf("%+v", out)
	}
	if strings.Contains(out.Message, "quota") {
		t.Fatal("cluster text reached the message")
	}
}

func TestAVerifyKilledAtAnyWriteLeavesNothingBehindAfterUndo(t *testing.T) {
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
				e.verify()
			}()
			e.sim.killBefore, e.sim.killAfter = 0, 0
			if out := e.undo(); !out.OK {
				t.Fatalf("kill at %d after=%v: %+v", n, after, out)
			}
			e.assertRestored()
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

var _ = bookinfotrace.Contract
