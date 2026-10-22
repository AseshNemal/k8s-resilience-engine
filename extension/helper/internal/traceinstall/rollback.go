package traceinstall

import (
	"context"
	"fmt"
	"sort"
	"strings"

	"resilience-engine/helper/internal/bookinfotrace"
	"resilience-engine/helper/internal/kube"
)

// rollback removes what this run created and records the result. It is used after a failed Apply (the approval covered
// it) and by Undo (which has its own approval). It touches only objects the record lists, each one only if the object
// in the cluster is the one that was created (same installation label, same UID), and never anything else.
//
// Order is a safety property. The injection label comes off before anything of Istio is removed, because the
// injection webhook fails closed: while the label is on and istiod is gone, no Pod in the namespace can be created.
// The Pods that were given a sidecar are recreated without one before Istio goes, because a sidecar without its
// control plane stops being able to renew its certificates. If any of that fails, Istio is left in place and what is
// left is reported.
func (r *run) rollback(ctx context.Context) ([]string, []bookinfotrace.Diff) {
	var remaining []string
	note := func(s string) {
		if s != "" {
			remaining = append(remaining, s)
		}
	}

	for _, ref := range []ObjectRef{clientPodRef(), virtualSvcRef(), destRuleRef(), reviewsV2Ref()} {
		note(r.remove(ctx, ref))
	}
	if e := r.rec.Entry(reviewsV2Ref()); e != nil && e.State == EntryDeleted {
		// Pods end asynchronously. Wait, so the check against the snapshot does not see a Pod that is still stopping.
		if err := r.waitUntil(ctx, "the reviews-v2 Pod to stop", r.d.Timeouts.Pod, func(ctx context.Context) (bool, error) {
			pods, err := r.podsOf(ctx, "reviews", "v2")
			return len(pods) == 0, err
		}); err != nil {
			note("the reviews-v2 Pod did not stop")
		}
	}

	safe := true
	if r.rec.LabelApplied {
		r.log("turn sidecar injection off", "started", "")
		if err := r.guard.SetInjection(ctx, false); err != nil {
			note("the istio-injection label could not be removed from the " + bookinfotrace.Contract.Namespace + " namespace")
			safe = false
		} else {
			r.rec.LabelApplied = false
			_ = r.save()
		}
	}
	if safe {
		for _, app := range r.rec.Recreated {
			if err := r.restoreWorkload(ctx, app); err != nil {
				note(err.Error())
				safe = false
				break
			}
		}
	}

	if !safe {
		note("Istio was left in place because the workloads are not back to normal yet")
	} else {
		note(r.remove(ctx, telemetryRef()))
		note(r.remove(ctx, serviceEntryRef()))
		r.removeIstio(ctx, note)
	}

	var diffs []bookinfotrace.Diff
	facts, err := bookinfotrace.Gather(ctx, r.d.Cluster)
	if err != nil {
		note("the cluster could not be read to confirm it matches the state recorded before the setup")
	} else {
		after := bookinfotrace.TakeSnapshot(facts, r.rec.Snapshot.Context, r.rec.Snapshot.TakenAt)
		diffs = bookinfotrace.Compare(r.rec.Snapshot, after)
		for _, diff := range diffs {
			note(fmt.Sprintf("%s: was %s, now %s", diff.What, nonEmpty(diff.Before), nonEmpty(diff.After)))
		}
	}

	r.rec.Remaining = remaining
	if len(remaining) == 0 {
		r.rec.Phase = PhaseRolledBack
		r.log("rollback", "done", "the cluster matches the state recorded before the setup")
	} else {
		r.rec.Phase = PhaseRollbackFailed
		r.log("rollback", "failed", fmt.Sprintf("%d item(s) remain", len(remaining)))
	}
	_ = r.save()
	return remaining, diffs
}

func nonEmpty(s string) string {
	if s == "" {
		return "none"
	}
	return s
}

// remove deletes one inventory object if, and only if, the cluster holds the object this run created. It returns text
// for the "remaining" list when the object could not be removed, and "" otherwise (removed, already gone, never created,
// or not ours and therefore left alone).
func (r *run) remove(ctx context.Context, ref ObjectRef) string {
	e := r.rec.Entry(ref)
	if e == nil || e.State == EntryPlanned || e.State == EntryDeleted || e.State == EntryForeign || e.State == EntryKept {
		return ""
	}
	res, err := ResourceFor(ref.APIVersion, ref.Kind)
	if err != nil {
		return ref.String() + " could not be removed"
	}
	obj, err := r.d.Cluster.Get(ctx, res, ref.Namespace, ref.Name)
	switch {
	case kube.IsNotFound(err):
		e.State = EntryDeleted
		_ = r.save()
		return ""
	case err != nil:
		return ref.String() + " could not be read, so it was not removed"
	}
	ours := obj.Labels[LabelInstallation] == r.rec.InstallationID && obj.Labels[LabelManagedBy] == bookinfotrace.Contract.ManagedBy &&
		(e.UID == "" || obj.UID == e.UID)
	if !ours {
		e.State = EntryForeign
		r.log("keep "+ref.String(), "skipped", "not created by this setup, so it is left alone")
		return ""
	}
	if err := r.guard.Delete(ctx, ref, obj.UID); err != nil && !kube.IsNotFound(err) {
		return ref.String() + " could not be removed"
	}
	e.State = EntryDeleted
	r.log("remove "+ref.String(), "done", "")
	return ""
}

// restoreWorkload deletes the Pods of one workload that carry a sidecar, one at a time, and waits until each
// replacement is Ready without one.
func (r *run) restoreWorkload(ctx context.Context, app string) error {
	version := ""
	for _, w := range bookinfotrace.Contract.RecreatedWorkloads {
		if w.App == app {
			version = w.Version
		}
	}
	if version == "" {
		return nil
	}
	pods, err := r.podsOf(ctx, app, version)
	if err != nil {
		return failed("the %s Pods could not be read, so they were not restored", app)
	}
	for _, pod := range pods {
		if !hasSidecar(pod) {
			continue
		}
		r.log("restore "+pod.Name, "started", "unavailable until its replacement is Ready")
		if err := r.guard.DeleteSidecarPod(ctx, pod); err != nil && !kube.IsNotFound(err) {
			return failed("the %s Pod %s could not be recreated without its sidecar", app, pod.Name)
		}
		old := pod.UID
		if err := r.waitUntil(ctx, "the replacement for "+pod.Name, r.d.Timeouts.Pod, func(ctx context.Context) (bool, error) {
			now, err := r.podsOf(ctx, app, version)
			if err != nil {
				return false, err
			}
			for _, p := range now {
				if p.UID != old && isReady(p) {
					if hasSidecar(p) {
						return false, failed("the replacement for %s still has a sidecar", pod.Name)
					}
					return true, nil
				}
			}
			return false, nil
		}); err != nil {
			return failed("the %s Pod %s was not back without its sidecar: %s", app, pod.Name, err.Error())
		}
		r.log("restore "+pod.Name, "done", "")
	}
	// A Pod this run deleted may not have been replaced yet (for example after an interruption): wait until the
	// workload is serving again, without a sidecar.
	if err := r.waitUntil(ctx, "the "+app+" Pod to be back", r.d.Timeouts.Pod, func(ctx context.Context) (bool, error) {
		now, err := r.podsOf(ctx, app, version)
		if err != nil {
			return false, err
		}
		for _, p := range now {
			if isReady(p) && !hasSidecar(p) {
				return true, nil
			}
		}
		return false, nil
	}); err != nil {
		return failed("the %s Pod was not back without its sidecar: %s", app, err.Error())
	}
	return nil
}

// removeIstio deletes the Istio objects the run created, in reverse creation order. A custom resource definition is
// kept if anything else still uses it, and the istio-system namespace is kept if anything else lives in it.
func (r *run) removeIstio(ctx context.Context, note func(string)) {
	var refs []ObjectRef
	for _, e := range r.rec.Inventory {
		if e.Stage == StageIstio {
			refs = append(refs, e.Ref)
		}
	}
	for i := len(refs) - 1; i >= 0; i-- {
		ref := refs[i]
		e := r.rec.Entry(ref)
		if e == nil || e.State == EntryPlanned || e.State == EntryDeleted || e.State == EntryForeign {
			continue
		}
		if e.State == EntryKept {
			// Kept earlier because something else used it. Look again: if that is no longer so, it goes now.
			e.State = EntryIntent
			if e.UID != "" {
				e.State = EntryCreated
			}
		}
		switch ref.Kind {
		case "CustomResourceDefinition":
			if used, err := r.crdInUse(ctx, ref.Name); used || err != nil {
				e.State = EntryKept
				note(ref.Name + " was kept: other Istio resources in the cluster still use it")
				continue
			}
		case "Namespace":
			if foreign, err := r.istioNamespaceHasOthers(ctx); foreign || err != nil {
				e.State = EntryKept
				note("the " + ref.Name + " namespace was kept: it holds objects this setup did not create")
				continue
			}
		}
		note(r.remove(ctx, ref))
	}
}

// crdInUse reports whether any custom resource of this definition exists. If that cannot be read, the answer is "yes":
// deleting a definition deletes every resource of it, so it is only done when it is proven empty.
func (r *run) crdInUse(ctx context.Context, name string) (bool, error) {
	crd, err := r.d.Cluster.Get(ctx, resCRD, "", name)
	if kube.IsNotFound(err) {
		return false, nil
	}
	if err != nil {
		return true, err
	}
	spec, _ := crd.Raw()["spec"].(map[string]any)
	names, _ := spec["names"].(map[string]any)
	group, _ := spec["group"].(string)
	plural, _ := names["plural"].(string)
	kind, _ := names["kind"].(string)
	scope, _ := spec["scope"].(string)
	versions, _ := spec["versions"].([]any)
	if group == "" || plural == "" || kind == "" || len(versions) == 0 {
		return true, fmt.Errorf("the definition could not be read")
	}
	for _, v := range versions {
		version, _ := v.(map[string]any)
		served, _ := version["served"].(bool)
		vn, _ := version["name"].(string)
		if !served || vn == "" {
			continue
		}
		items, err := r.d.Cluster.List(ctx, kube.Resource{Group: group, Version: vn, Plural: plural, Kind: kind, Namespaced: scope == "Namespaced"}, "")
		if err != nil || len(items) > 0 {
			return true, err
		}
	}
	return false, nil
}

var istioNamespaceKinds = []kube.Resource{kube.ConfigMaps, kube.Secrets, kube.Services, kube.Deployments, kube.StatefulSets, kube.DaemonSets,
	kube.Pods, kube.ServiceAccounts, kube.Roles, kube.RoleBindings, kube.Jobs, kube.CronJobs, kube.Ingresses, kube.PersistentVolumeClaims}

// generatedByIstiod are objects istiod makes for itself in its namespace; they go with the namespace.
func generatedByIstiod(res kube.Resource, o kube.Object) bool {
	switch res.Kind {
	case "ConfigMap":
		return o.Name == "kube-root-ca.crt" || o.Name == "istio-ca-root-cert" || strings.HasPrefix(o.Name, "istio-")
	case "Secret":
		return o.Name == "istio-ca-secret"
	case "Pod":
		return o.Labels["app"] == "istiod"
	}
	return false
}

func (r *run) istioNamespaceHasOthers(ctx context.Context) (bool, error) {
	owned := map[string]bool{}
	for _, e := range r.rec.Inventory {
		if e.Ref.Namespace == bookinfotrace.Contract.IstioNamespace {
			owned[e.Ref.Kind+"/"+e.Ref.Name] = true
		}
	}
	for _, res := range istioNamespaceKinds {
		items, err := r.d.Cluster.List(ctx, res, bookinfotrace.Contract.IstioNamespace)
		if err != nil {
			return true, err
		}
		for _, o := range items {
			if !owned[res.Kind+"/"+o.Name] && !generatedByIstiod(res, o) {
				return true, nil
			}
		}
	}
	return false, nil
}

// Undo removes the tracing setup this installation created and checks the cluster against the snapshot taken before.
func Undo(ctx context.Context, d Deps, p Params) Outcome {
	rec, err := d.Store.Load(p.Identity)
	if err != nil {
		return recordProblem(err)
	}
	if rec == nil {
		if others := d.Store.ForContext(p.ContextName); len(others) > 0 {
			return fail(StateIdentityChanged, "A setup record exists for a different cluster that had this context name (the cluster was probably recreated). Nothing was changed, and nothing is removed on the strength of a record that belongs to another cluster.")
		}
		return fail(StateNoRecord, "There is no record of a tracing setup made by this extension on this cluster, so nothing was removed. Undo only removes what a recorded setup created.")
	}
	if rec.Phase == PhaseRolledBack {
		o := Outcome{OK: true, State: StateNothingToUndo, Message: "Nothing to undo: the setup was already removed.", Setup: SummaryOf(rec)}
		return o
	}

	var restarts []string
	r := newRun(d, rec)
	for _, app := range rec.Recreated {
		for _, w := range bookinfotrace.Contract.RecreatedWorkloads {
			if w.App != app {
				continue
			}
			pods, _ := r.podsOf(ctx, app, w.Version)
			for _, pod := range pods {
				if hasSidecar(pod) {
					restarts = append(restarts, pod.Name)
				}
			}
		}
	}
	sort.Strings(restarts)
	removesV2 := false
	if e := rec.Entry(reviewsV2Ref()); e != nil && (e.State == EntryCreated || e.State == EntryIntent) {
		removesV2 = true
	}
	req, err := bookinfotrace.UndoDialog(p.ContextName, removesV2, restarts)
	if err != nil {
		return fail(StateError, "The list of changes is too long to show in full, so it cannot be approved. Nothing was changed.")
	}
	if refused := approval(ctx, d, req); refused != nil {
		refused.Setup = SummaryOf(rec)
		return *refused
	}

	rec.Phase = PhaseRollingBack
	r.log("undo", "started", "approved by the person")
	rctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), d.Timeouts.Rollback)
	defer cancel()
	remaining, diffs := r.rollback(rctx)
	if len(remaining) > 0 {
		return Outcome{State: StateRollbackIncomplet, Setup: SummaryOf(rec), Diffs: diffs,
			Message: "Some of the setup could not be removed: " + strings.Join(remaining, "; ") + ". The record is kept; Undo can be run again."}
	}
	return Outcome{OK: true, State: StateUndone, Setup: SummaryOf(rec), Message: "The tracing setup was removed and the cluster matches the state recorded before it started."}
}
