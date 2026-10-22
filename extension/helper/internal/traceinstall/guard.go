package traceinstall

import (
	"context"
	"errors"
	"fmt"

	"resilience-engine/helper/internal/bookinfotrace"
	"resilience-engine/helper/internal/kube"
)

// ErrNotAllowed is returned, before any request is made, for a write outside the allow-list.
var ErrNotAllowed = errors.New("that change is outside what the tracing setup is allowed to do")

// Guard is the only way this package writes to a cluster. It checks the allow-list in code before every request:
//
//   - a create must name an object of the inventory, in its own namespace, carrying the management label and this
//     installation's ID;
//   - a delete must name an object of the inventory and carry the UID this run recorded, so the server itself refuses
//     to delete an object that is not the one this run created;
//   - a Pod may be deleted only if the approved plan named it;
//   - the only label change is istio-injection on the bookinfo namespace.
//
// Everything else (another namespace, Secrets, RBAC outside the rendered inventory, the original Bookinfo
// Deployments) is refused whatever the caller asks. The inventory is fixed when the Guard is built.
type Guard struct {
	w            kube.RawWriter
	installation string
	allowed      map[ObjectRef]bool
	pods         map[string]bool
}

// NewGuard builds the allow-list from the planned inventory and the Pods the approved plan named.
func NewGuard(w kube.RawWriter, installationID string, inventory []Entry, plannedPods []string) *Guard {
	g := &Guard{w: w, installation: installationID, allowed: map[ObjectRef]bool{}, pods: map[string]bool{}}
	for _, e := range inventory {
		g.allowed[e.Ref] = true
	}
	for _, p := range plannedPods {
		g.pods[p] = true
	}
	return g
}

func asMap(v any) map[string]any { m, _ := v.(map[string]any); return m }

// Stamp returns a copy of a document with the management labels on its metadata. Nothing else in it changes.
func Stamp(body map[string]any, installationID string) map[string]any {
	out := make(map[string]any, len(body))
	for k, v := range body {
		out[k] = v
	}
	meta := map[string]any{}
	for k, v := range asMap(body["metadata"]) {
		meta[k] = v
	}
	labels := map[string]any{}
	for k, v := range asMap(meta["labels"]) {
		labels[k] = v
	}
	labels[LabelManagedBy] = bookinfotrace.Contract.ManagedBy
	labels[LabelInstallation] = installationID
	meta["labels"] = labels
	out["metadata"] = meta
	return out
}

// Create creates one object of the inventory and returns its UID.
func (g *Guard) Create(ctx context.Context, body map[string]any) (ObjectRef, string, error) {
	ref := refOf(body)
	if !g.allowed[ref] {
		return ref, "", fmt.Errorf("%w: %s", ErrNotAllowed, ref)
	}
	res, err := ResourceFor(ref.APIVersion, ref.Kind)
	if err != nil || res.Namespaced != (ref.Namespace != "") {
		return ref, "", fmt.Errorf("%w: %s", ErrNotAllowed, ref)
	}
	labels := asMap(asMap(body["metadata"])["labels"])
	if labels[LabelManagedBy] != bookinfotrace.Contract.ManagedBy || labels[LabelInstallation] != g.installation || g.installation == "" {
		return ref, "", fmt.Errorf("%w: %s is not labelled as part of this installation", ErrNotAllowed, ref)
	}
	uid, err := g.w.CreateRaw(ctx, res, ref.Namespace, body)
	return ref, uid, err
}

// Delete removes one object of the inventory, and only if its UID is the one the run recorded.
func (g *Guard) Delete(ctx context.Context, ref ObjectRef, uid string) error {
	if !g.allowed[ref] || uid == "" {
		return fmt.Errorf("%w: %s", ErrNotAllowed, ref)
	}
	res, err := ResourceFor(ref.APIVersion, ref.Kind)
	if err != nil || res.Namespaced != (ref.Namespace != "") {
		return fmt.Errorf("%w: %s", ErrNotAllowed, ref)
	}
	return g.w.DeleteRaw(ctx, res, ref.Namespace, ref.Name, uid)
}

// DeletePod removes a Pod the approved plan named (so its Deployment recreates it), by UID.
func (g *Guard) DeletePod(ctx context.Context, name, uid string) error {
	if !g.pods[name] || uid == "" {
		return fmt.Errorf("%w: pod %s was not in the approved plan", ErrNotAllowed, name)
	}
	return g.w.DeleteRaw(ctx, resPod, bookinfotrace.Contract.Namespace, name, uid)
}

const injectionLabel = "istio-injection"

// SetInjection turns sidecar injection on or off for the bookinfo namespace. It is the only label change possible.
func (g *Guard) SetInjection(ctx context.Context, on bool) error {
	var value *string
	if on {
		v := "enabled"
		value = &v
	}
	return g.w.PatchLabels(ctx, resNamespace, "", bookinfotrace.Contract.Namespace, map[string]*string{injectionLabel: value})
}
