package traceinstall

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/bookinfotrace"
	"resilience-engine/helper/internal/kube"
)

func indexOf(writes []string, exact string) int {
	for i, w := range writes {
		if w == exact {
			return i
		}
	}
	return -1
}

func TestApplyCreatesExactlyTheApprovedObjectsInSafeOrderAndEndsApplied(t *testing.T) {
	e := newEnv(t)
	out := e.apply()
	if !out.OK || out.State != StateApplied {
		t.Fatalf("%+v", out)
	}
	w := e.sim.writes
	if len(w) != 42 { // namespace + 32 Istio objects + ServiceEntry + Telemetry + label + 3 Pods + reviews-v2 + DR + VS
		t.Fatalf("%d writes: %v", len(w), w)
	}
	order := []string{
		"create Namespace istio-system", "create Deployment istio-system/istiod", "create ServiceEntry bookinfo/trace-demo-jaeger-otlp",
		"create Telemetry bookinfo/trace-demo", "patch namespaces bookinfo", "delete Pod bookinfo/ratings-v1-p3", "delete Pod bookinfo/details-v1-p2",
		"delete Pod bookinfo/productpage-v1-p1", "create Deployment bookinfo/reviews-v2", "create DestinationRule bookinfo/trace-demo-reviews",
		"create VirtualService bookinfo/trace-demo-reviews",
	}
	last := -1
	for _, step := range order {
		at := indexOf(w, step)
		if at < 0 || at <= last {
			t.Fatalf("%q is missing or out of order (at %d, after %d) in %v", step, at, last, w)
		}
		last = at
	}
	for _, line := range w {
		if strings.HasPrefix(line, "delete ") && !strings.HasPrefix(line, "delete Pod bookinfo/") {
			t.Errorf("Apply deleted something other than a planned Pod: %s", line)
		}
		if strings.Contains(line, "-v1 ") || strings.Contains(line, "Deployment bookinfo/productpage") {
			t.Errorf("an original Bookinfo Deployment was written: %s", line)
		}
	}

	// the end state
	for _, app := range []string{"ratings", "details", "productpage"} {
		pods, _ := (&run{d: e.deps}).podsOf(context.Background(), app, "v1")
		if len(pods) != 1 || !hasSidecar(pods[0]) {
			t.Errorf("%s: %+v", app, pods)
		}
	}
	if pods, _ := (&run{d: e.deps}).podsOf(context.Background(), "reviews", "v1"); hasSidecar(pods[0]) {
		t.Error("reviews-v1 was not restarted and must not have a sidecar")
	}
	if ns, _ := e.sim.get(kube.Namespaces, "", "bookinfo"); ns.obj.Labels["istio-injection"] != "enabled" {
		t.Error("the namespace is not labelled")
	}
	for name, digest := range map[string]string{"productpage-v1": "digest-productpage", "details-v1": "digest-details", "ratings-v1": "digest-ratings", "reviews-v1": "digest-reviews"} {
		if d, _ := e.sim.get(kube.Deployments, "bookinfo", name); d.obj.SpecDigest != digest {
			t.Errorf("%s was edited", name)
		}
	}
	rec := e.record()
	if rec.Phase != PhaseApplied || rec.InstallationID == "" || len(rec.Recreated) != 3 || !rec.LabelApplied {
		t.Fatalf("%+v", rec)
	}
	created := 0
	for _, entry := range rec.Inventory {
		if entry.State == EntryCreated {
			created++
			if entry.UID == "" {
				t.Errorf("%s has no UID", entry.Ref)
			}
		}
	}
	if created != 38 { // istio-system, Istio's 32 objects, ServiceEntry, Telemetry, reviews-v2, DestinationRule, VirtualService
		t.Errorf("%d objects recorded as created", created)
	}
}

func TestTheApprovalDialogShowsEveryChangeTheDowntimeAndTheRollbackConsent(t *testing.T) {
	e := newEnv(t)
	e.apply()
	if len(e.prompter.requests) != 1 {
		t.Fatalf("%d dialogs", len(e.prompter.requests))
	}
	body := e.prompter.requests[0].Body
	for _, want := range []string{"docker-desktop", "CHANGES the cluster", "ratings-v1-p3", "DOWNTIME", "removes only what this run created", "downloads istioctl"} {
		if !strings.Contains(body, want) {
			t.Errorf("missing %q:\n%s", want, body)
		}
	}
	// the second time the tool is already stored, so there is nothing to download and the dialog must not say so
	e2 := newEnv(t)
	e2.deps.Tools.Dir = e.deps.Tools.Dir
	e2.fetcher.body = nil
	e2.apply()
	if strings.Contains(e2.prompter.requests[0].Body, "downloads") {
		t.Errorf("a stored tool is not downloaded again:\n%s", e2.prompter.requests[0].Body)
	}
}

func TestDecliningTimingOutOrHavingNoDialogChangesNothingAndDownloadsNothing(t *testing.T) {
	for name, p := range map[string]*fakePrompter{
		"declined":   {decision: approve.Declined},
		"timed out":  {decision: approve.TimedOut},
		"no channel": {err: approve.ErrNoApprovalChannel},
		"error":      {err: errors.New("boom")},
	} {
		e := newEnv(t)
		e.deps.Prompter = p
		out := e.apply()
		if out.OK {
			t.Errorf("%s: %+v", name, out)
		}
		e.assertNothingWritten()
		if len(e.fetcher.calls) != 0 || len(e.exec.args) != 0 {
			t.Errorf("%s: the tool was fetched or run before approval", name)
		}
	}
}

func TestABlockedPlanShowsNoDialogAndWritesNothing(t *testing.T) {
	e := newEnv(t)
	e.sim.put(kube.CustomResourceDefinitions, kube.Object{Name: "virtualservices.networking.istio.io"})
	out := e.apply()
	if out.State != StatePlanBlocked || out.Plan == nil || len(e.prompter.requests) != 0 {
		t.Fatalf("%+v", out)
	}
	e.assertNothingWritten()

	e = newEnv(t)
	e.meas.SwapUsedMiB = nil // a reading that cannot be taken
	if out := e.apply(); out.State != StatePlanBlocked {
		t.Fatalf("%+v", out)
	}
	e.assertNothingWritten()
}

func TestAnApprovalIsVoidIfTheClusterChangesWhileThePersonDecides(t *testing.T) {
	e := newEnv(t)
	e.prompter.hook = func() { // the Pod the plan named restarts by itself
		delete(e.sim.objs, key("pods", "bookinfo", "ratings-v1-p3"))
		e.sim.newPod("ratings", "v1")
	}
	out := e.apply()
	if out.State != StatePlanChanged {
		t.Fatalf("%+v", out)
	}
	e.assertNothingWritten()
	if len(e.fetcher.calls) != 0 {
		t.Error("the tool was fetched after a void approval")
	}
}

func TestEveryToolFailureStopsBeforeAnyWrite(t *testing.T) {
	cases := map[string]func(e *env){
		"bad checksum":     func(e *env) { e.fetcher.body = []byte("tampered") },
		"download failure": func(e *env) { e.fetcher.err = errors.New("network down") },
		"unexpected render": func(e *env) {
			e.exec.out = append(e.exec.out, []byte("---\napiVersion: v1\nkind: Secret\nmetadata:\n  name: extra\n  namespace: istio-system\n")...)
		},
		"render fails": func(e *env) { e.exec.code = 1 },
	}
	for name, setup := range cases {
		e := newEnv(t)
		setup(e)
		out := e.apply()
		if out.State != StateToolUnavailable {
			t.Errorf("%s: %+v", name, out)
		}
		e.assertNothingWritten()
	}
	e := newEnv(t)
	e.deps.Tools.Lookup = nil
	e.deps.Tools.GOOS, e.deps.Tools.GOARCH = "windows", "arm64"
	if out := e.apply(); out.State != StatePlatform {
		t.Fatalf("%+v", out)
	}
	e.assertNothingWritten()
	if len(e.prompter.requests) != 0 {
		t.Error("the person was asked about a setup that cannot run on this platform")
	}
}

// writesInASuccessfulRun is the number of cluster writes Apply makes when nothing goes wrong.
func writesInASuccessfulRun(t *testing.T) int {
	e := newEnv(t)
	if out := e.apply(); !out.OK {
		t.Fatalf("%+v", out)
	}
	return len(e.sim.writes)
}

func TestAFailureAtAnySingleWriteRollsBackToExactlyTheStartingState(t *testing.T) {
	total := writesInASuccessfulRun(t)
	for n := 1; n <= total; n++ {
		n := n
		t.Run(fmt.Sprintf("write %d", n), func(t *testing.T) {
			t.Parallel()
			e := newEnv(t)
			e.sim.failWrite[n] = errors.New("SECRET-API-TEXT 10.0.0.7")
			out := e.apply()
			if out.OK || out.State != StateApplyFailed {
				t.Fatalf("(%s): %+v", e.sim.writes[len(e.sim.writes)-1], out)
			}
			e.assertRestored()
			if rec := e.record(); rec.Phase != PhaseRolledBack || rec.Failure == nil || len(rec.Remaining) != 0 {
				t.Fatalf("%+v", rec)
			}
			if strings.Contains(out.Message, "SECRET-API-TEXT") || strings.Contains(fmt.Sprint(e.record().Steps), "SECRET-API-TEXT") {
				t.Fatal("cluster text reached the output or the record")
			}
		})
	}
}

func TestAProcessKilledAtAnySingleWriteCanBeUndoneFromTheRecord(t *testing.T) {
	total := writesInASuccessfulRun(t)
	for _, after := range []bool{false, true} {
		for n := 1; n <= total; n++ {
			after, n := after, n
			t.Run(fmt.Sprintf("kill at write %d after=%v", n, after), func(t *testing.T) {
				t.Parallel()
				e := newEnv(t)
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
					e.apply()
				}()
				e.sim.killBefore, e.sim.killAfter = 0, 0
				e.sim.writes = nil

				if out := e.apply(); out.State != StateSetupIncomplete || len(e.sim.writes) != 0 {
					t.Fatalf("Apply started over an unfinished setup: %+v %v", out, e.sim.writes)
				}
				out := e.undo()
				if !out.OK || out.State != StateUndone {
					t.Fatalf("%+v", out)
				}
				e.assertRestored()
				if e.record().Phase != PhaseRolledBack {
					t.Fatalf("%s", e.record().Phase)
				}
			})
		}
	}
}

func TestCancellingTheRunStillRollsBack(t *testing.T) {
	for _, at := range []int{1, 3, 8, 15} { // during the Istio wait, the Pod restarts, and later
		e := newEnv(t)
		ctx, cancel := context.WithCancel(context.Background())
		e.clock.onSleep = func(n int) {
			if n == at {
				cancel()
			}
		}
		out := Apply(ctx, e.deps, e.params)
		cancel()
		if out.State != StateApplyFailed || !strings.Contains(out.Message, "interrupted") {
			t.Fatalf("cancel at sleep %d: %+v", at, out)
		}
		e.clock.onSleep = nil
		e.assertRestored()
	}
}

func TestAFailedGateRollsBackAndSaysWhich(t *testing.T) {
	cases := map[string]func(e *env){
		"istiod too large":              func(e *env) { e.meas.IstiodMiB = f(400) },
		"istiod not measurable":         func(e *env) { e.meas.IstiodMiB = nil },
		"sidecar over its cap":          func(e *env) { e.meas.SidecarMaxMiB = f(140) },
		"sidecar not measurable":        func(e *env) { e.meas.SidecarMaxMiB = nil },
		"docker memory over at the end": func(e *env) { e.meas.VMUsedMiB = f(3300) },
	}
	for name, setup := range cases {
		e := newEnv(t)
		// readings are fine until the install starts, as they would be: the plan and gate 0 pass first
		e.deps.Measure = func(context.Context) (m bookinfotrace.Measurements) {
			e.measures++
			if len(e.sim.writes) > 0 {
				setup(e)
			}
			return e.meas
		}
		out := e.apply()
		if out.State != StateApplyFailed {
			t.Errorf("%s: %+v", name, out)
			continue
		}
		e.assertRestored()
	}
}

func TestMemoryClimbingWhileWaitingStopsTheSetup(t *testing.T) {
	e := newEnv(t)
	e.deps.Measure = func(context.Context) bookinfotrace.Measurements {
		m := e.meas
		if e.clock.sleeps > 2 { // memory climbs once the waiting starts
			m.VMUsedMiB = f(3400)
		}
		return m
	}
	out := e.apply()
	if out.State != StateApplyFailed || !strings.Contains(out.Message, "while waiting") {
		t.Fatalf("%+v", out)
	}
	e.assertRestored()
}

func TestMemoryThatCannotBeReadWhileWaitingStopsTheSetup(t *testing.T) {
	e := newEnv(t)
	e.sim.istiodDelay = 100 * time.Second // long enough for three unreadable readings, 15 s apart
	e.deps.Measure = func(context.Context) bookinfotrace.Measurements {
		m := e.meas
		if e.clock.sleeps > 0 {
			m.VMUsedMiB, m.SwapUsedMiB = nil, nil
		}
		return m
	}
	out := e.apply()
	if out.State != StateApplyFailed || !strings.Contains(out.Message, "could not be read") {
		t.Fatalf("%+v", out)
	}
	e.assertRestored()
}

func TestAMissingSidecarIsCaughtAndRolledBack(t *testing.T) {
	e := newEnv(t)
	e.sim.injectBroken = true
	out := e.apply()
	if out.State != StateApplyFailed || !strings.Contains(out.Message, "without a sidecar") {
		t.Fatalf("%+v", out)
	}
	e.assertRestored()
}

func TestIstiodThatNeverBecomesReadyTimesOutAndIsRemoved(t *testing.T) {
	e := newEnv(t)
	e.sim.istiodDelay = 24 * time.Hour
	out := e.apply()
	if out.State != StateApplyFailed || !strings.Contains(out.Message, "istiod did not become ready") {
		t.Fatalf("%+v", out)
	}
	e.assertRestored()
}

func TestAJaegerAddressThatChangesAfterApprovalStopsTheSetup(t *testing.T) {
	e := newEnv(t)
	calls := 0
	e.deps.Discover = func(context.Context) (bookinfotrace.Address, error) {
		calls++
		a := e.address
		if calls > 2 { // plan, plan again, then the check just before the ServiceEntry
			a.IPv4 = "192.168.65.1"
		}
		return a, nil
	}
	out := e.apply()
	if out.State != StateApplyFailed || !strings.Contains(out.Message, "Jaeger") {
		t.Fatalf("%+v", out)
	}
	e.assertRestored()
}

func TestACustomResourceThatIsNotServedYetIsRetried(t *testing.T) {
	e := newEnv(t)
	e.sim.crdLag = 2
	if out := e.apply(); !out.OK {
		t.Fatalf("%+v", out)
	}
}

func TestAnExistingObjectWithAnInventoryNameIsNeverAdoptedOrDeleted(t *testing.T) {
	e := newEnv(t)
	e.sim.put(kube.Namespaces, kube.Object{Name: "ignored"}) // unrelated
	e.sim.put(kube.Resource{Group: "networking.istio.io", Version: "v1", Plural: "serviceentries", Kind: "ServiceEntry", Namespaced: true},
		kube.Object{Name: "trace-demo-jaeger-otlp", Namespace: "bookinfo", UID: "someone-elses", Labels: map[string]string{"owner": "someone"}})
	e.sim.baseKeys[key("serviceentries", "bookinfo", "trace-demo-jaeger-otlp")] = true
	e.sim.baseKeys[key("namespaces", "", "ignored")] = true
	out := e.apply()
	// Everything this run created is removed, except the ServiceEntry definition: someone else's ServiceEntry uses it.
	if out.State != StateRollbackIncomplet || !strings.Contains(out.Message, "already taken") || !strings.Contains(out.Message, "serviceentries.networking.istio.io was kept") {
		t.Fatalf("%+v", out)
	}
	if o, ok := e.sim.get(kube.Resource{Plural: "serviceentries", Namespaced: true}, "bookinfo", "trace-demo-jaeger-otlp"); !ok || o.obj.UID != "someone-elses" {
		t.Fatal("somebody else's object was touched")
	}
	if left := e.sim.leftovers(); len(left) != 1 || left[0] != "customresourcedefinitions//serviceentries.networking.istio.io" {
		t.Fatalf("only the shared definition may remain: %v", left)
	}
	foreign := 0
	for _, entry := range e.record().Inventory {
		if entry.State == EntryForeign {
			foreign++
		}
	}
	if foreign != 1 {
		t.Errorf("%d entries marked foreign", foreign)
	}
}

func TestARollbackThatCannotFinishKeepsTheRecordAndSaysWhatRemains(t *testing.T) {
	e := newEnv(t)
	e.sim.failWrite[40] = errors.New("boom")                                          // Apply fails late...
	e.sim.failAlways["delete:Telemetry bookinfo/trace-demo"] = errors.New("API down") // ...and the rollback cannot delete one object
	out := e.apply()
	if out.State != StateRollbackIncomplet || !strings.Contains(out.Message, "Telemetry") {
		t.Fatalf("%+v", out)
	}
	rec := e.record()
	if rec.Phase != PhaseRollbackFailed || len(rec.Remaining) == 0 {
		t.Fatalf("%+v", rec)
	}
	// the Telemetry CR still exists, so its CRD is kept rather than deleting someone's resource
	if _, ok := e.sim.get(kube.CustomResourceDefinitions, "", "telemetries.telemetry.istio.io"); !ok {
		t.Error("a CRD in use was deleted")
	}
	if again := e.apply(); again.State != StateSetupIncomplete {
		t.Errorf("Apply must refuse until Undo finishes: %+v", again)
	}
	// once the API recovers, Undo finishes the job from the record
	delete(e.sim.failAlways, "delete:Telemetry bookinfo/trace-demo")
	if undone := e.undo(); !undone.OK {
		t.Fatalf("%+v", undone)
	}
	e.assertRestored()
}

func TestApplyingTwiceIsIdempotentAndNoticesAMissingPiece(t *testing.T) {
	e := newEnv(t)
	e.apply()
	e.sim.writes = nil
	if out := e.apply(); !out.OK || out.State != StateAlreadyApplied || len(e.sim.writes) != 0 {
		t.Fatalf("%+v %v", out, e.sim.writes)
	}
	delete(e.sim.objs, key("telemetries", "bookinfo", "trace-demo"))
	if out := e.apply(); out.State != StateSetupIncomplete || len(e.sim.writes) != 0 {
		t.Fatalf("%+v", out)
	}
}

func TestAnExistingReviewsV2StopsTheSetupBeforeAnyDialogOrWrite(t *testing.T) {
	e := newEnv(t)
	one := 1
	e.sim.put(kube.Deployments, kube.Object{Name: "reviews-v2", Namespace: "bookinfo", Replicas: &one, ReadyReplicas: &one, SpecDigest: "digest-v2"})
	ready := true
	e.sim.put(kube.Pods, kube.Object{Name: "reviews-v2-pre", Namespace: "bookinfo", Labels: map[string]string{"app": "reviews", "version": "v2"}, Containers: []string{"reviews"}, Ready: &ready, Phase: "Running"})
	out := e.apply()
	if out.State != StatePlanBlocked || out.Plan == nil {
		t.Fatalf("%+v", out)
	}
	blocked := false
	for _, b := range out.Plan.Blockers {
		blocked = blocked || b.Code == "REVIEWS_V2_EXISTS"
	}
	if !blocked || len(e.prompter.requests) != 0 || len(e.sim.writes) != 0 {
		t.Fatalf("blocked=%v dialogs=%d writes=%v", blocked, len(e.prompter.requests), e.sim.writes)
	}
	if _, kept := e.sim.get(kube.Deployments, "bookinfo", "reviews-v2"); !kept {
		t.Fatal("the existing reviews-v2 was touched")
	}
}

// If reviews-v2 appears between the plan and the creation, the create finds the name taken: it is never adopted.
func TestAReviewsV2ThatAppearsAfterTheApprovalIsNeverAdopted(t *testing.T) {
	e := newEnv(t)
	e.sim.failWrite = map[int]error{}
	e.prompter.hook = nil
	one := 1
	created := false
	e.clock.onSleep = func(int) {
		if !created && indexOf(e.sim.writes, "patch namespaces bookinfo") >= 0 { // after the label, before reviews-v2
			created = true
			e.sim.put(kube.Deployments, kube.Object{Name: "reviews-v2", Namespace: "bookinfo", UID: "someone-elses", Replicas: &one, ReadyReplicas: &one})
			e.sim.baseKeys[key("deployments", "bookinfo", "reviews-v2")] = true
		}
	}
	out := e.apply()
	if out.OK || !created {
		t.Fatalf("%+v", out)
	}
	if dep, ok := e.sim.get(kube.Deployments, "bookinfo", "reviews-v2"); !ok || dep.obj.UID != "someone-elses" {
		t.Fatal("somebody else's reviews-v2 was touched")
	}
}

func TestNoClusterTextReachesAnyOutcomeOrTheRecordWhenReadsFail(t *testing.T) {
	const secret = "SECRET-LIST-TEXT-10.1.2.3"
	for _, plural := range []string{"pods", "deployments", "customresourcedefinitions", "configmaps", "services", "serviceaccounts"} {
		for _, phase := range []string{"apply", "applied-then-undo", "applied-then-verify"} {
			e := newEnv(t)
			switch phase {
			case "apply":
				e.sim.failList["list:"+plural] = errors.New(secret)
			default:
				if out := e.apply(); !out.OK {
					t.Fatalf("setup: %+v", out)
				}
				e.sim.failList["list:"+plural] = errors.New(secret)
			}
			var out Outcome
			switch phase {
			case "applied-then-undo":
				out = e.undo()
			case "applied-then-verify":
				out = e.verify()
			default:
				out = e.apply()
			}
			raw, _ := json.Marshal(out)
			rec, _ := os.ReadFile(e.deps.Store.path(e.params.Identity))
			if strings.Contains(string(raw), secret) || strings.Contains(string(rec), secret) {
				t.Errorf("%s failing during %s leaked cluster text:\n%s", plural, phase, raw)
			}
		}
	}
}

// ---- the approval is for the exact content of the Istio objects, not just their names ----

func TestARenderWithTheSameNamesButDifferentContentIsRefusedBeforeAnyWrite(t *testing.T) {
	widened := strings.Replace(string(richRender()),
		"kind: ClusterRole\nmetadata:\n  name: istiod-clusterrole-istio-system\n",
		"kind: ClusterRole\nmetadata:\n  name: istiod-clusterrole-istio-system\nrules:\n- apiGroups: ['*']\n  resources: ['*']\n  verbs: ['*']\n", 1)
	if widened == string(richRender()) {
		t.Fatal("the test did not change the render")
	}
	e := newEnv(t)
	e.exec.out = []byte(widened) // every name, kind and count is the reviewed one; one object grants everything
	out := e.apply()
	if out.OK || out.State != StateToolUnavailable || !strings.Contains(out.Message, "differ in content from the reviewed render") {
		t.Fatalf("%+v", out)
	}
	e.assertNothingWritten()
}

func TestAChangedImageOrAnyOtherFieldIsAlsoRefused(t *testing.T) {
	for name, edit := range map[string]func(string) string{
		"an added field": func(s string) string {
			return strings.Replace(s, "kind: ServiceAccount\nmetadata:\n  name: istiod\n", "kind: ServiceAccount\nmetadata:\n  name: istiod\nautomountServiceAccountToken: false\n", 1)
		},
		"a changed label": func(s string) string {
			return strings.Replace(s, "kind: Service\nmetadata:\n  name: istiod\n", "kind: Service\nmetadata:\n  name: istiod\n  labels:\n    extra: x\n", 1)
		},
	} {
		e := newEnv(t)
		changed := edit(string(richRender()))
		if changed == string(richRender()) {
			t.Fatalf("%s: the test did not change the render", name)
		}
		e.exec.out = []byte(changed)
		if out := e.apply(); out.State != StateToolUnavailable {
			t.Errorf("%s: %+v", name, out)
		}
		e.assertNothingWritten()
	}
}

func TestFormattingDifferencesDoNotChangeTheDigest(t *testing.T) {
	plain := richRender()
	crlf := []byte("# generated\r\n" + strings.ReplaceAll(string(plain), "\n", "\r\n"))
	reordered := strings.Replace(string(plain), "apiVersion: apiextensions.k8s.io/v1\nkind: CustomResourceDefinition\nmetadata:", "kind: CustomResourceDefinition\napiVersion: apiextensions.k8s.io/v1\nmetadata:", 1)
	a, err := DigestOfRendered(plain)
	if err != nil {
		t.Fatal(err)
	}
	for name, variant := range map[string][]byte{"Windows line endings and a comment": crlf, "key order": []byte(reordered)} {
		got, err := DigestOfRendered(variant)
		if err != nil || got != a {
			t.Errorf("%s changed the digest: %v %v", name, got, err)
		}
	}
	e := newEnv(t)
	e.exec.out = crlf
	if out := e.apply(); !out.OK {
		t.Fatalf("a formatting-only difference must not stop the setup: %+v", out)
	}
}

func TestObjectsChangedInMemoryAfterTheRenderAreCaughtBeforeTheFirstWrite(t *testing.T) {
	e := newEnv(t)
	manifests, err := e.deps.Tools.Render(bg, "istioctl")
	if err != nil {
		t.Fatal(err)
	}
	rec := NewRecord(install, e.params.Identity, bookinfotrace.PlanResult{}, bookinfotrace.Snapshot{}, "fp", e.deps.Tools.Reviewed(), PlannedInventory(), e.clock.Now())
	if err := e.deps.Store.Save(rec, e.clock.Now()); err != nil {
		t.Fatal(err)
	}
	manifests[3].Body["rules"] = []any{map[string]any{"verbs": []any{"*"}}} // changed after it was approved
	r := newRun(e.deps, rec)
	err = r.stages(bg, manifests, documents{})
	if err == nil || !strings.Contains(err.Error(), "changed after they were approved") {
		t.Fatalf("%v", err)
	}
	if len(e.sim.writes) != 0 {
		t.Fatalf("something was written: %v", e.sim.writes)
	}
}

func TestTheDialogAndTheRecordCarryTheContentDigest(t *testing.T) {
	e := newEnv(t)
	e.apply()
	digest := e.deps.Tools.Reviewed()
	if body := e.prompter.requests[0].Body; !strings.Contains(body, digest[:12]) || !strings.Contains(body, "must match the reviewed content") {
		t.Fatalf("the approval does not show what the content must match:\n%s", body)
	}
	if rec := e.record(); rec.RenderDigest != digest {
		t.Fatalf("the record binds %q, not the approved %q", rec.RenderDigest, digest)
	}
}

func TestTheContentCheckCannotBeSwitchedOffAndProductionUsesTheCompiledInDigest(t *testing.T) {
	if (ToolEnv{}).Reviewed() != ReviewedRenderDigest || len(ReviewedRenderDigest) != 64 {
		t.Fatal("an unset digest must mean the compiled-in one, never 'no check'")
	}
	// a render with no matching reviewed digest is refused
	env := ToolEnv{GOOS: "linux", Dir: t.TempDir(), Exec: &recordingExec{out: richRender()}}
	if _, err := env.Render(bg, "istioctl"); !errors.Is(err, ErrRenderContentDiffer) {
		t.Fatalf("%v", err)
	}
}
