package traceinstall

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/bookinfotrace"
	"resilience-engine/helper/internal/kube"
)

// ResolveIdentity reads what makes this cluster this cluster: the UID of its kube-system namespace, together with the
// context name, the API server address and the fingerprint of its certificate authority, which the caller already holds.
func ResolveIdentity(ctx context.Context, r kube.Reader, contextName, server, caSha256 string) (ClusterIdentity, error) {
	ns, err := r.Get(ctx, kube.Namespaces, "", "kube-system")
	if err != nil || ns.UID == "" {
		return ClusterIdentity{}, errors.New("the cluster's identity could not be read")
	}
	return ClusterIdentity{Context: contextName, Server: server, CASha256: caSha256, KubeSystemUID: ns.UID}, nil
}

// plan makes the same plan the Plan command shows, and keeps the facts it was made from.
func (d Deps) plan(ctx context.Context, contextName string) (bookinfotrace.PlanResult, bookinfotrace.Facts, error) {
	facts, err := bookinfotrace.Gather(ctx, d.Cluster)
	if err != nil {
		p := bookinfotrace.PlanResult{Context: contextName, Blockers: []bookinfotrace.Blocker{{Code: "CLUSTER_READ_FAILED", Message: "The cluster could not be read completely, so nothing can be planned."}}}
		return p, facts, err
	}
	var addr *bookinfotrace.Address
	found, addrErr := d.discover(ctx)
	if addrErr == nil {
		addr = &found
	}
	return bookinfotrace.BuildPlan(facts, addr, addrErr, d.measure(ctx), contextName), facts, nil
}

// approval shows the dialog and turns every answer but "approved" into the outcome of the command.
func approval(ctx context.Context, d Deps, req approve.Request) *Outcome {
	decision, err := d.Prompter.Confirm(ctx, req)
	switch {
	case errors.Is(err, approve.ErrNoApprovalChannel), errors.Is(err, approve.ErrUnsupportedPlatform):
		o := fail(StateApprovalNA, "no approval dialog is available on this system, so nothing was changed")
		return &o
	case err != nil:
		o := fail(StateError, "the approval dialog could not be shown, so nothing was changed")
		return &o
	}
	switch decision {
	case approve.Approved:
		return nil
	case approve.TimedOut:
		o := fail(StateTimedOut, "no answer was given, so nothing was changed")
		return &o
	}
	o := fail(StateDeclined, "you declined, so nothing was changed")
	return &o
}

func recordProblem(err error) Outcome {
	return fail(StateRecordUnusable, err.Error()+" Nothing was changed.")
}

// Apply installs the tracing setup. Nothing in the cluster changes until the person approved a dialog that lists
// every change; the plan is then made again and must be identical; the record exists before the first write; and any
// failure afterwards rolls back what this run created.
func Apply(ctx context.Context, d Deps, p Params) Outcome {
	rec, err := d.Store.Load(p.Identity)
	if err != nil {
		return recordProblem(err)
	}
	if rec != nil && !rec.Terminal() {
		o := fail(StateSetupIncomplete, "An earlier setup did not finish or could not be fully removed. Use Undo to finish removing it before setting up again.")
		o.Setup = SummaryOf(rec)
		return o
	}
	if rec != nil && rec.Phase == PhaseApplied {
		return alreadyApplied(ctx, d, rec)
	}

	needDownload, _, err := d.Tools.NeedsDownload()
	if err != nil {
		return fail(StatePlatform, err.Error())
	}
	plan, _, err := d.plan(ctx, p.ContextName)
	if err != nil || !plan.Ready {
		o := fail(StatePlanBlocked, "The setup is blocked for the reasons listed. Nothing was changed.")
		o.Plan = &plan
		return o
	}
	extras := bookinfotrace.DialogExtras{}
	if needDownload {
		extras.Download = "istioctl " + IstioVersion + " from github.com/istio/istio (about 30 MB, checked against a built-in SHA-256, stored privately). It is only used to render the Istio objects offline."
	}
	req, err := bookinfotrace.ApplyDialog(plan, extras)
	if err != nil {
		return fail(StateError, "The list of changes is too long to show in full, so it cannot be approved. Nothing was changed.")
	}
	if refused := approval(ctx, d, req); refused != nil {
		refused.Plan = &plan
		return *refused
	}

	// The approval is for this exact plan. If the cluster changed while the person was deciding, it is void.
	again, facts, err := d.plan(ctx, p.ContextName)
	if err != nil || !again.Ready || bookinfotrace.Fingerprint(again) != bookinfotrace.Fingerprint(plan) {
		o := fail(StatePlanChanged, "The cluster changed while you were deciding, so the approval no longer matches. Nothing was changed; plan again.")
		o.Plan = &again
		return o
	}

	// Everything below up to the first write touches nothing in the cluster: tool, render, documents.
	bin, err := d.Tools.Ensure(ctx)
	if err != nil {
		return fail(StateToolUnavailable, err.Error()+" Nothing was changed.")
	}
	istioObjects, err := d.Tools.Render(ctx, bin)
	if err != nil {
		return fail(StateToolUnavailable, err.Error()+" Nothing was changed.")
	}
	docs, err := bookinfoDocuments(again.Jaeger.IPv4)
	if err != nil {
		return fail(StateError, err.Error()+" Nothing was changed.")
	}

	id, err := d.newID()
	if err != nil {
		return fail(StateError, "an installation ID could not be made. Nothing was changed.")
	}
	_, reviewsExists := facts.Deployments[bookinfotrace.Contract.CreatedFromOriginal.Name]
	snapshot := bookinfotrace.TakeSnapshot(facts, p.ContextName, d.clock().Now().UTC().Format("2006-01-02T15:04:05Z"))
	rec = NewRecord(id, p.Identity, again, snapshot, bookinfotrace.Fingerprint(again), PlannedInventory(reviewsExists), d.clock().Now())
	if err := d.Store.Save(rec, d.clock().Now()); err != nil {
		return recordProblem(err)
	}

	r := newRun(d, rec)
	if err := r.stages(ctx, istioObjects, docs, reviewsExists); err != nil {
		return r.abort(ctx, err)
	}
	rec.Phase = PhaseApplied
	r.log("setup", "done", "")
	if err := r.save(); err != nil {
		return r.abort(ctx, err)
	}
	return Outcome{OK: true, State: StateApplied, Message: "Tracing is set up. Use Verify to produce and check real traces.", Plan: &again, Setup: SummaryOf(rec)}
}

// documents holds the objects of the bookinfo namespace, ready to create.
type documents struct {
	serviceEntry, telemetry, destinationRule, virtualService, reviewsV2 map[string]any
}

func bookinfoDocuments(jaegerIPv4 string) (documents, error) {
	var docs documents
	var err error
	if docs.serviceEntry, err = serviceEntryDocument(jaegerIPv4); err != nil {
		return docs, err
	}
	if docs.telemetry, err = documentOf(fileTelemetry, "Telemetry"); err != nil {
		return docs, err
	}
	if docs.destinationRule, err = documentOf(fileRoute, "DestinationRule"); err != nil {
		return docs, err
	}
	if docs.virtualService, err = documentOf(fileRoute, "VirtualService"); err != nil {
		return docs, err
	}
	if docs.reviewsV2, err = documentOf(fileReviewsV2, "Deployment"); err != nil {
		return docs, err
	}
	meta, _ := docs.reviewsV2["metadata"].(map[string]any)
	meta["namespace"] = bookinfotrace.Contract.Namespace // the original manifest has none; kubectl -n supplies it
	return docs, nil
}

// alreadyApplied is the idempotent answer to a second Apply: it checks that what the record says exists still does.
func alreadyApplied(ctx context.Context, d Deps, rec *Record) Outcome {
	for _, e := range rec.Inventory {
		if e.State != EntryCreated || e.Stage == StageVerify {
			continue
		}
		res, err := ResourceFor(e.Ref.APIVersion, e.Ref.Kind)
		if err != nil {
			continue
		}
		obj, err := d.Cluster.Get(ctx, res, e.Ref.Namespace, e.Ref.Name)
		if err != nil || obj.UID != e.UID {
			o := fail(StateSetupIncomplete, fmt.Sprintf("The setup is recorded as applied but %s is missing or was replaced. Use Undo, then set up again.", e.Ref))
			o.Setup = SummaryOf(rec)
			return o
		}
	}
	return Outcome{OK: true, State: StateAlreadyApplied, Message: "Tracing is already set up. Nothing was changed.", Setup: SummaryOf(rec)}
}

// stages runs the staged setup. Each stage ends with a gate.
func (r *run) stages(ctx context.Context, istio []Manifest, docs documents, reviewsExists bool) error {
	r.stage("memory check")
	if err := r.gate(ctx, bookinfotrace.Gate0, "memory check before starting"); err != nil {
		return err
	}

	r.stage(StageIstio)
	r.log("install Istio "+IstioVersion, "started", "")
	if err := r.create(ctx, namespaceDocument(bookinfotrace.Contract.IstioNamespace)); err != nil {
		return err
	}
	for _, m := range istio {
		if err := r.create(ctx, m.Body); err != nil {
			return err
		}
	}
	if err := r.waitUntil(ctx, "istiod", r.d.Timeouts.Istiod, func(ctx context.Context) (bool, error) {
		return r.deploymentReady(ctx, bookinfotrace.Contract.IstioNamespace, "istiod")
	}); err != nil {
		return err
	}
	r.log("install Istio "+IstioVersion, "done", "")
	if err := r.gate(ctx, bookinfotrace.Gate1, "memory check after the Istio install"); err != nil {
		return err
	}

	r.stage(StageConfig)
	current, err := r.d.discover(ctx)
	if err != nil || current.IPv4 != r.rec.JaegerIPv4 {
		return failed("the Jaeger receiver can no longer be reached at the address that was approved")
	}
	for _, doc := range []map[string]any{docs.serviceEntry, docs.telemetry} {
		if err := r.create(ctx, doc); err != nil {
			return err
		}
	}
	r.rec.LabelApplied = true // written first: if the helper dies after the patch, Undo still removes the label
	if err := r.save(); err != nil {
		return err
	}
	r.log("turn sidecar injection on", "started", "")
	if err := r.guard.SetInjection(ctx, true); err != nil {
		return describe("turning sidecar injection on", err)
	}

	r.stage(StageWorkload)
	for _, name := range r.rec.PlannedPods {
		if err := r.replacePod(ctx, name); err != nil {
			return err
		}
	}
	if !reviewsExists {
		r.log("create reviews-v2", "started", "")
		if err := r.create(ctx, docs.reviewsV2); err != nil {
			return err
		}
		if err := r.waitUntil(ctx, "reviews-v2", r.d.Timeouts.Pod, func(ctx context.Context) (bool, error) {
			pods, err := r.podsOf(ctx, "reviews", "v2")
			for _, p := range pods {
				if isReady(p) {
					if !hasSidecar(p) {
						return false, failed("reviews-v2 started without a sidecar")
					}
					return true, nil
				}
			}
			return false, err
		}); err != nil {
			return err
		}
		r.log("create reviews-v2", "done", "")
	}

	r.stage(StageRoute)
	for _, doc := range []map[string]any{docs.destinationRule, docs.virtualService} {
		if err := r.create(ctx, doc); err != nil {
			return err
		}
	}
	return r.gate(ctx, bookinfotrace.Gate2, "memory and readiness check at the end")
}

func (r *run) stage(name string) { r.currentStage = name }

// replacePod deletes one planned Pod so its Deployment recreates it with a sidecar, and waits for the replacement.
// The Pod's app is written to the record first: if the helper dies mid-way, Undo knows which workload to restore.
func (r *run) replacePod(ctx context.Context, name string) error {
	c := bookinfotrace.Contract
	pod, err := r.d.Cluster.Get(ctx, resPod, c.Namespace, name)
	if err != nil {
		return failed("Pod %s no longer exists as planned; plan again", name)
	}
	app, version := pod.Labels["app"], pod.Labels["version"]
	if hasSidecar(*pod) {
		return failed("Pod %s already has a sidecar", name)
	}
	if !contains(r.rec.Recreated, app) {
		r.rec.Recreated = append(r.rec.Recreated, app)
	}
	if err := r.save(); err != nil {
		return err
	}
	r.log("restart "+name, "started", "unavailable until its replacement is Ready")
	if err := r.guard.DeletePod(ctx, name, pod.UID); err != nil && !kube.IsNotFound(err) {
		return describe("deleting Pod "+name, err)
	}
	if err := r.waitUntil(ctx, "the replacement for "+name, r.d.Timeouts.Pod, func(ctx context.Context) (bool, error) {
		pods, err := r.podsOf(ctx, app, version)
		if err != nil {
			return false, err
		}
		for _, p := range pods {
			if p.UID == pod.UID || !isReady(p) {
				continue
			}
			if !hasSidecar(p) {
				return false, failed("the replacement for %s started without a sidecar", name)
			}
			return true, nil
		}
		return false, nil
	}); err != nil {
		return err
	}
	r.log("restart "+name, "done", "")
	return nil
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

// abort stops a failed run and rolls back what it created, even when the run was interrupted: the rollback gets its
// own deadline and does not inherit the cancellation.
func (r *run) abort(ctx context.Context, cause error) Outcome {
	reason := cause.Error()
	r.rec.Failure = &FailureInfo{Stage: r.currentStage, Reason: reason, At: r.d.clock().Now().UTC().Format("2006-01-02T15:04:05Z")}
	r.rec.Phase = PhaseRollingBack
	r.log("rolling back", "started", "because: "+reason)
	rctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), r.d.Timeouts.Rollback)
	defer cancel()
	remaining, diffs := r.rollback(rctx)
	o := Outcome{Setup: SummaryOf(r.rec), Diffs: diffs}
	if len(remaining) == 0 {
		o.State = StateApplyFailed
		o.Message = "The setup stopped: " + reason + ". Everything this run created was removed and the cluster matches the state recorded before it started."
		return o
	}
	o.State = StateRollbackIncomplet
	o.Message = "The setup stopped: " + reason + ". Some of what it created could not be removed (" + strings.Join(remaining, "; ") + "). The record is kept; use Undo to try again."
	return o
}
