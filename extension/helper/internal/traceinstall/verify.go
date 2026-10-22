package traceinstall

import (
	"context"
	"fmt"
	"strings"

	"resilience-engine/helper/internal/bookinfotrace"
	"resilience-engine/helper/internal/kube"
)

// InstrumentedPod is one Pod that must carry a sidecar for traces to exist.
type InstrumentedPod struct {
	Workload string `json:"workload"`
	Pod      string `json:"pod"`
	Sidecar  bool   `json:"sidecar"`
	Ready    bool   `json:"ready"`
}

// VerifyResult is the cluster-side half of Verify: what the helper can see from the cluster. It does NOT say that
// traces arrived; that is read from Jaeger afterwards (GET /_extension/tracing/trace-path), because only the spans in
// Jaeger prove that an instrumented Pod really reached it.
type VerifyResult struct {
	AppliedAddress string            `json:"appliedAddress"`
	CurrentAddress string            `json:"currentAddress"`
	AddressDrift   bool              `json:"addressDrift"`
	Instrumented   []InstrumentedPod `json:"instrumented"`
	RequestsSent   bool              `json:"requestsSent"` // the temporary client Pod ran to completion
	Readings       string            `json:"readings"`
}

// workloads that must have a sidecar for the demo traces: the three that were restarted and reviews-v2.
func instrumentedWorkloads() [][2]string {
	var out [][2]string
	for _, w := range bookinfotrace.Contract.RecreatedWorkloads {
		out = append(out, [2]string{w.App, w.Version})
	}
	return append(out, [2]string{"reviews", "v2"})
}

func serviceEntryAddress(o *kube.Object) string {
	spec, _ := o.Raw()["spec"].(map[string]any)
	endpoints, _ := spec["endpoints"].([]any)
	if len(endpoints) == 0 {
		return ""
	}
	endpoint, _ := endpoints[0].(map[string]any)
	address, _ := endpoint["address"].(string)
	return address
}

// check is the read-only half shared by Verify and RunTraffic. It changes nothing in the cluster and shows no dialog: it
// reads the saved record, re-discovers the Jaeger address and compares it with the ServiceEntry (drift is reported),
// confirms every created object is still the one this run created, and requires a Ready sidecar on ratings, details,
// productpage and reviews-v2. A non-nil Outcome is the reason it stopped.
func check(ctx context.Context, d Deps, p Params) (*Record, *VerifyResult, *Outcome) {
	rec, err := d.Store.Load(p.Identity)
	if err != nil {
		o := recordProblem(err)
		return nil, nil, &o
	}
	if rec == nil || rec.Phase != PhaseApplied {
		o := fail(StateNotApplied, "Tracing is not set up on this cluster, so there is nothing to verify.")
		o.Setup = SummaryOf(rec)
		return nil, nil, &o
	}
	if existing := alreadyApplied(ctx, d, rec); !existing.OK {
		existing.State = StateVerifyFailed
		return nil, nil, &existing
	}

	result := &VerifyResult{Instrumented: []InstrumentedPod{}}
	stop := func(format string, args ...any) (*Record, *VerifyResult, *Outcome) {
		o := fail(StateVerifyFailed, fmt.Sprintf(format, args...))
		o.Verify, o.Setup = result, SummaryOf(rec)
		return nil, nil, &o
	}

	entry, err := d.Cluster.Get(ctx, resServiceEntry, bookinfotrace.Contract.Namespace, serviceEntryRef().Name)
	if err != nil {
		return stop("The ServiceEntry could not be read.")
	}
	result.AppliedAddress = serviceEntryAddress(entry)
	current, err := d.discover(ctx)
	if err != nil {
		return stop("The Jaeger receiver can no longer be reached from the cluster's nodes (%s).", err.Error())
	}
	result.CurrentAddress = current.IPv4
	if bookinfotrace.Drift(result.AppliedAddress, current) {
		result.AddressDrift = true
		return stop("The Jaeger address changed: the ServiceEntry points at %s, but the cluster's nodes now reach it at %s. Undo and set up again.", result.AppliedAddress, current.IPv4)
	}

	r := newRun(d, rec)
	for _, w := range instrumentedWorkloads() {
		pods, err := r.podsOf(ctx, w[0], w[1])
		if err != nil {
			return stop("The %s Pods could not be read.", w[0])
		}
		if len(pods) == 0 {
			return stop("There is no %s-%s Pod.", w[0], w[1])
		}
		for _, pod := range pods {
			result.Instrumented = append(result.Instrumented, InstrumentedPod{Workload: w[0] + "-" + w[1], Pod: pod.Name, Sidecar: hasSidecar(pod), Ready: isReady(pod)})
			if !hasSidecar(pod) || !isReady(pod) {
				return stop("%s has no working sidecar, so it cannot produce traces.", pod.Name)
			}
		}
	}
	return rec, result, nil
}

// Verify is read-only. It writes nothing to the cluster, creates no Pod and asks nothing: it reports whether the setup
// is in place and healthy (see check) and what memory looks like. Producing traffic is a separate operation with its
// own approval (RunTraffic). Whether traces arrived is read from Jaeger by the page.
func Verify(ctx context.Context, d Deps, p Params) Outcome {
	if rec, err := d.Store.Load(p.Identity); err == nil && rec != nil && rec.Phase == PhaseRolledBack {
		return verifyCleanup(ctx, d, rec) // after Undo, "verify" means: is the cluster really back to how it was?
	}
	rec, result, bad := check(ctx, d, p)
	if bad != nil {
		return *bad
	}
	m := d.measure(ctx)
	result.Readings = readingText(m)
	return Outcome{OK: true, State: StateChecksPassed, Verify: result, Setup: SummaryOf(rec),
		Message: "The setup is in place: every sidecar is ready and the Jaeger address is unchanged. Nothing was changed. Run test traffic to produce traces, or read the traces Jaeger already has."}
}

// RunTraffic creates one temporary Pod inside the mesh that sends real requests to productpage, after its own native
// approval. It runs the read-only checks first, so nothing is asked when the setup is not healthy; it checks again after
// approval, because the cluster may have changed while the person was deciding; and it deletes the Pod afterwards, also
// when something fails, is cancelled or killed (Undo removes it from the record). The Pod makes no span itself and never
// talks to Jaeger.
func RunTraffic(ctx context.Context, d Deps, p Params) Outcome {
	rec, result, bad := check(ctx, d, p)
	if bad != nil {
		return *bad
	}
	stop := func(format string, args ...any) Outcome {
		o := fail(StateVerifyFailed, fmt.Sprintf(format, args...))
		o.Verify, o.Setup = result, SummaryOf(rec)
		return o
	}
	// room for one more small Pod
	m := d.measure(ctx)
	ready := true
	m.PodsReady = &ready
	result.Readings = readingText(m)
	if decision := bookinfotrace.Evaluate(bookinfotrace.Gate2, rec.Baseline, m, d.Thresholds); !decision.Proceed {
		return stop("Memory is too tight to run the client Pod: %s.", strings.Join(decision.Reasons, "; "))
	}
	doc, err := documentOf(fileClient, "Pod")
	if err != nil {
		return stop("The client Pod manifest could not be read.")
	}
	req, err := bookinfotrace.TrafficDialog(p.ContextName)
	if err != nil {
		return stop("The description of the Pod is too long to show in full, so it cannot be approved.")
	}
	if refused := approval(ctx, d, req); refused != nil {
		refused.Verify, refused.Setup = result, SummaryOf(rec)
		return *refused
	}
	if _, _, bad := check(ctx, d, p); bad != nil {
		bad.Message = "The setup changed while you were deciding, so nothing was created. " + bad.Message
		return *bad
	}

	r := newRun(d, rec)
	r.log("send real requests", "started", "a temporary Pod sends requests to productpage")
	created := false
	defer func() {
		if created {
			cleanup, cancel := context.WithTimeout(context.WithoutCancel(ctx), d.Timeouts.Pod)
			defer cancel()
			r.remove(cleanup, clientPodRef())
		}
	}()
	if err := r.create(ctx, doc); err != nil {
		created = true // the intent was recorded; the cleanup looks at the cluster
		return stop("The client Pod could not be created: %s.", err.Error())
	}
	created = true
	finished := false
	err = r.waitUntil(ctx, "the client Pod to finish its requests", d.Timeouts.Client, func(ctx context.Context) (bool, error) {
		pod, err := d.Cluster.Get(ctx, resPod, bookinfotrace.Contract.Namespace, clientPodRef().Name)
		if err != nil {
			return false, err
		}
		switch pod.Phase {
		case "Succeeded":
			finished = true
			return true, nil
		case "Failed":
			return false, failed("the client Pod failed")
		}
		return false, nil
	})
	if err != nil || !finished {
		r.log("send real requests", "failed", "")
		reason := "it did not finish"
		if err != nil {
			reason = err.Error()
		}
		return stop("The client Pod did not finish sending requests: %s.", reason)
	}
	result.RequestsSent = true
	r.log("send real requests", "done", "")
	return Outcome{OK: true, State: StateTrafficSent, Verify: result, Setup: SummaryOf(rec),
		Message: "Real requests were sent from an instrumented Pod. The traces that follow are read from Jaeger."}
}

// verifyCleanup is the read-only check after Undo, independent of Undo's own result: the cluster is read again and
// compared with the snapshot taken before the setup (namespace labels, Deployment specs, replicas, containers per Pod,
// so no sidecar is left), and every object this installation created and deleted must really be gone. An object of the
// same name that does not carry this installation's label is someone else's and is not counted.
func verifyCleanup(ctx context.Context, d Deps, rec *Record) Outcome {
	var problems []string
	facts, err := bookinfotrace.Gather(ctx, d.Cluster)
	if err != nil {
		return fail(StateVerifyFailed, "The cluster could not be read to check the cleanup.")
	}
	diffs := bookinfotrace.Compare(rec.Snapshot, bookinfotrace.TakeSnapshot(facts, rec.Snapshot.Context, rec.Snapshot.TakenAt))
	for _, diff := range diffs {
		problems = append(problems, fmt.Sprintf("%s: was %s, now %s", diff.What, nonEmpty(diff.Before), nonEmpty(diff.After)))
	}
	for _, e := range rec.Inventory {
		if e.State != EntryDeleted {
			continue
		}
		res, err := ResourceFor(e.Ref.APIVersion, e.Ref.Kind)
		if err != nil {
			continue
		}
		obj, err := d.Cluster.Get(ctx, res, e.Ref.Namespace, e.Ref.Name)
		switch {
		case kube.IsNotFound(err):
		case err != nil:
			problems = append(problems, e.Ref.String()+" could not be read")
		case obj.Labels[LabelInstallation] == rec.InstallationID:
			problems = append(problems, e.Ref.String()+" is still there")
		}
	}
	if len(problems) > 0 {
		o := fail(StateVerifyFailed, "The cleanup is not complete: "+strings.Join(problems, "; ")+".")
		o.Diffs, o.Setup = diffs, SummaryOf(rec)
		return o
	}
	return Outcome{OK: true, State: StateCleanupVerified, Setup: SummaryOf(rec),
		Message: "The cluster matches the state recorded before the setup, and every object this setup created is gone."}
}
