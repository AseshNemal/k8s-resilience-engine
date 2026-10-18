// Package rbac owns the least-privilege read-only access the extension needs, and nothing else.
//
// The desired objects below must stay identical to deploy/rbac/resilience-readonly.yaml (a test
// compares them). Three operations matter for safety:
//
//   - Inspect reads the cluster and classifies each object: absent, exactly as expected, correct but
//     missing the ownership label, or drifted. It also finds any OTHER binding that grants the
//     service account more. It never writes.
//   - Plan turns an inspection into the exact list of changes an approval dialog must show.
//   - Apply creates or repairs the objects, only after the user approved that plan.
//
// Renewal uses Inspect only: a token is issued only when every object is exactly as expected, so a
// widened ClusterRole or an extra binding can never lead to an over-privileged token, and revoked
// access is never silently recreated.
package rbac

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"sort"
	"strings"

	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
)

const (
	Namespace      = "resilience-engine"
	Name           = "resilience-engine-reader"
	ManagedByKey   = "app.kubernetes.io/managed-by"
	ManagedByValue = "resilience-engine-extension"
)

type Kind string

const (
	KindNamespace          Kind = "Namespace"
	KindServiceAccount     Kind = "ServiceAccount"
	KindClusterRole        Kind = "ClusterRole"
	KindClusterRoleBinding Kind = "ClusterRoleBinding"
)

type State string

const (
	Absent     State = "absent"
	Matches    State = "matches"
	NeedsLabel State = "needs-label"
	Drifted    State = "drifted"
)

// Overall summarises a Report for the UI and for renewal decisions.
type Overall string

const (
	OverallApproved    Overall = "approved"            // every object exactly as expected and labelled
	OverallApprovedOld Overall = "approved-unlabelled" // content exact, ownership label missing (created by the old script)
	OverallAbsent      Overall = "absent"              // nothing exists: never approved, or fully revoked
	OverallPartial     Overall = "partial"             // some objects exist, some do not
	OverallDrifted     Overall = "drifted"             // an object differs from what was approved
	OverallOverGranted Overall = "over-granted"        // another binding grants the service account more
)

var (
	// ErrOverGranted means another binding grants the service account extra access. Authorize
	// refuses to continue; deleting someone else's bindings is the user's decision, not ours.
	ErrOverGranted = errors.New("another role binding grants the extension's service account extra access")
	// ErrForbidden means the user's own kubeconfig may not manage RBAC on this cluster.
	ErrForbidden = errors.New("your kubeconfig is not allowed to manage RBAC on this cluster")
)

type ObjectState struct {
	Kind   Kind   `json:"kind"`
	Name   string `json:"name"`
	State  State  `json:"state"`
	Detail string `json:"detail,omitempty"`
	// Owner is the value of the managed-by label, if any. A different owner means the object is not
	// ours, and Revoke will never delete it.
	Owner string `json:"owner,omitempty"`
}

// foreign reports whether another tool, not this extension, has labelled the object as its own.
func (o ObjectState) foreign() bool { return o.Owner != "" && o.Owner != ManagedByValue }

type ExtraGrant struct {
	Kind      string `json:"kind"` // ClusterRoleBinding or RoleBinding
	Namespace string `json:"namespace,omitempty"`
	Name      string `json:"name"`
}

type Report struct {
	Objects     []ObjectState `json:"objects"`
	ExtraGrants []ExtraGrant  `json:"extraGrants,omitempty"`
}

func labels() map[string]string { return map[string]string{ManagedByKey: ManagedByValue} }

func desiredRules() []rbacv1.PolicyRule {
	return []rbacv1.PolicyRule{
		{APIGroups: []string{""}, Resources: []string{"nodes", "pods"}, Verbs: []string{"get", "list"}},
		{APIGroups: []string{"apps"}, Resources: []string{"deployments"}, Verbs: []string{"get", "list"}},
	}
}

func desiredSubject() rbacv1.Subject {
	return rbacv1.Subject{Kind: rbacv1.ServiceAccountKind, Name: Name, Namespace: Namespace}
}

func desiredRoleRef() rbacv1.RoleRef {
	return rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "ClusterRole", Name: Name}
}

// Permissions describes in plain words what the credential can do, for the approval dialog.
func Permissions() []string {
	return []string{
		"Read (get, list) nodes and pods",
		"Read (get, list) deployments",
		"No access to Secrets, ConfigMaps or any other resource; no create, update, patch or delete",
	}
}

// normalizeRules makes rule comparison independent of ordering inside each rule.
func normalizeRules(in []rbacv1.PolicyRule) []rbacv1.PolicyRule {
	out := make([]rbacv1.PolicyRule, 0, len(in))
	for _, r := range in {
		c := rbacv1.PolicyRule{
			APIGroups:       sortedCopy(r.APIGroups),
			Resources:       sortedCopy(r.Resources),
			Verbs:           sortedCopy(r.Verbs),
			ResourceNames:   sortedCopy(r.ResourceNames),
			NonResourceURLs: sortedCopy(r.NonResourceURLs),
		}
		out = append(out, c)
	}
	sort.Slice(out, func(i, j int) bool { return fmt.Sprint(out[i]) < fmt.Sprint(out[j]) })
	return out
}

func sortedCopy(in []string) []string {
	if len(in) == 0 {
		return []string{}
	}
	out := append([]string(nil), in...)
	sort.Strings(out)
	return out
}

func labelState(have map[string]string) (State, string) {
	switch v := have[ManagedByKey]; v {
	case ManagedByValue:
		return Matches, ""
	case "":
		return NeedsLabel, "ownership label missing"
	default:
		return Drifted, fmt.Sprintf("managed by %q, not by this extension", v)
	}
}

// Inspect reads the four objects and any other bindings of the service account. It never writes.
func Inspect(ctx context.Context, cs kubernetes.Interface) (Report, error) {
	var r Report

	ns, err := cs.CoreV1().Namespaces().Get(ctx, Namespace, metav1.GetOptions{})
	r.add(KindNamespace, Namespace, err, func() string { return ns.Labels[ManagedByKey] }, func() (State, string) { return labelState(ns.Labels) })
	if err != nil && !apierrors.IsNotFound(err) {
		return r, wrap(err)
	}

	sa, err := cs.CoreV1().ServiceAccounts(Namespace).Get(ctx, Name, metav1.GetOptions{})
	r.add(KindServiceAccount, Name, err, func() string { return sa.Labels[ManagedByKey] }, func() (State, string) { return labelState(sa.Labels) })
	if err != nil && !apierrors.IsNotFound(err) {
		return r, wrap(err)
	}

	cr, err := cs.RbacV1().ClusterRoles().Get(ctx, Name, metav1.GetOptions{})
	r.add(KindClusterRole, Name, err, func() string { return cr.Labels[ManagedByKey] }, func() (State, string) {
		if cr.AggregationRule != nil {
			return Drifted, "has an aggregation rule, so it can gain permissions from other roles"
		}
		if !reflect.DeepEqual(normalizeRules(cr.Rules), normalizeRules(desiredRules())) {
			return Drifted, "rules differ from the approved read-only set"
		}
		return labelState(cr.Labels)
	})
	if err != nil && !apierrors.IsNotFound(err) {
		return r, wrap(err)
	}

	crb, err := cs.RbacV1().ClusterRoleBindings().Get(ctx, Name, metav1.GetOptions{})
	r.add(KindClusterRoleBinding, Name, err, func() string { return crb.Labels[ManagedByKey] }, func() (State, string) {
		if crb.RoleRef != desiredRoleRef() {
			return Drifted, "binds a different role"
		}
		if len(crb.Subjects) != 1 || crb.Subjects[0] != desiredSubject() {
			return Drifted, "binds subjects other than the extension's service account"
		}
		return labelState(crb.Labels)
	})
	if err != nil && !apierrors.IsNotFound(err) {
		return r, wrap(err)
	}

	extras, err := extraGrants(ctx, cs)
	if err != nil {
		return r, wrap(err)
	}
	r.ExtraGrants = extras
	return r, nil
}

func (r *Report) add(kind Kind, name string, err error, owner func() string, classify func() (State, string)) {
	if err != nil {
		if apierrors.IsNotFound(err) {
			r.Objects = append(r.Objects, ObjectState{Kind: kind, Name: name, State: Absent})
		}
		return
	}
	state, detail := classify()
	r.Objects = append(r.Objects, ObjectState{Kind: kind, Name: name, State: state, Detail: detail, Owner: owner()})
}

// extraGrants finds bindings, other than the extension's own, that name its service account.
func extraGrants(ctx context.Context, cs kubernetes.Interface) ([]ExtraGrant, error) {
	var out []ExtraGrant
	hit := func(subjects []rbacv1.Subject) bool {
		for _, s := range subjects {
			if s.Kind == rbacv1.ServiceAccountKind && s.Name == Name && s.Namespace == Namespace {
				return true
			}
		}
		return false
	}
	crbs, err := cs.RbacV1().ClusterRoleBindings().List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, err
	}
	for _, b := range crbs.Items {
		if b.Name != Name && hit(b.Subjects) {
			out = append(out, ExtraGrant{Kind: "ClusterRoleBinding", Name: b.Name})
		}
	}
	rbs, err := cs.RbacV1().RoleBindings(metav1.NamespaceAll).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, err
	}
	for _, b := range rbs.Items {
		if hit(b.Subjects) {
			out = append(out, ExtraGrant{Kind: "RoleBinding", Namespace: b.Namespace, Name: b.Name})
		}
	}
	return out, nil
}

func (r Report) states() map[State]int {
	m := map[State]int{}
	for _, o := range r.Objects {
		m[o.State]++
	}
	return m
}

func (r Report) Overall() Overall {
	s := r.states()
	switch {
	case len(r.ExtraGrants) > 0:
		return OverallOverGranted
	case s[Drifted] > 0:
		return OverallDrifted
	case s[Absent] == len(r.Objects):
		return OverallAbsent
	case s[Absent] > 0:
		return OverallPartial
	case s[NeedsLabel] > 0:
		return OverallApprovedOld
	default:
		return OverallApproved
	}
}

// Usable reports whether a token may be issued: every object exists with exactly the approved
// content and nothing else grants the service account more. A missing ownership label alone does
// not make the access unsafe (the old setup script never set one), so it does not block renewal.
func (r Report) Usable() bool {
	o := r.Overall()
	return o == OverallApproved || o == OverallApprovedOld
}

// Change is one line of what an approval would do, worded for the dialog.
type Change struct {
	Action string `json:"action"` // create | update | label
	Kind   Kind   `json:"kind"`
	Name   string `json:"name"`
	Detail string `json:"detail,omitempty"`
}

func (c Change) String() string {
	s := fmt.Sprintf("%s %s %s", strings.ToUpper(c.Action[:1])+c.Action[1:], c.Kind, c.Name)
	if c.Detail != "" {
		s += " (" + c.Detail + ")"
	}
	return s
}

// Plan lists exactly what Apply would change. An empty plan means no cluster change is needed.
func Plan(r Report) []Change {
	var out []Change
	for _, o := range r.Objects {
		switch o.State {
		case Absent:
			out = append(out, Change{Action: "create", Kind: o.Kind, Name: o.Name})
		case NeedsLabel:
			out = append(out, Change{Action: "label", Kind: o.Kind, Name: o.Name, Detail: "add " + ManagedByKey + " so it is recognised as the extension's"})
		case Drifted:
			out = append(out, Change{Action: "update", Kind: o.Kind, Name: o.Name, Detail: o.Detail + "; will be reset to the approved read-only set"})
		}
	}
	return out
}

func wrap(err error) error {
	if apierrors.IsForbidden(err) {
		return fmt.Errorf("%w: %s", ErrForbidden, apierrors.ReasonForError(err))
	}
	return err
}

// Apply performs a Plan. It refuses to run when another binding over-grants the service account.
func Apply(ctx context.Context, cs kubernetes.Interface, r Report) error {
	if len(r.ExtraGrants) > 0 {
		return ErrOverGranted
	}
	byKind := map[Kind]State{}
	for _, o := range r.Objects {
		byKind[o.Kind] = o.State
	}

	if st := byKind[KindNamespace]; st != Matches {
		if err := ensureNamespace(ctx, cs, st); err != nil {
			return wrap(err)
		}
	}
	if st := byKind[KindServiceAccount]; st != Matches {
		if err := ensureServiceAccount(ctx, cs, st); err != nil {
			return wrap(err)
		}
	}
	if st := byKind[KindClusterRole]; st != Matches {
		if err := ensureClusterRole(ctx, cs, st); err != nil {
			return wrap(err)
		}
	}
	if st := byKind[KindClusterRoleBinding]; st != Matches {
		if err := ensureBinding(ctx, cs, st); err != nil {
			return wrap(err)
		}
	}
	return nil
}

func ensureNamespace(ctx context.Context, cs kubernetes.Interface, st State) error {
	api := cs.CoreV1().Namespaces()
	if st == Absent {
		_, err := api.Create(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: Namespace, Labels: labels()}}, metav1.CreateOptions{})
		return err
	}
	cur, err := api.Get(ctx, Namespace, metav1.GetOptions{})
	if err != nil {
		return err
	}
	cur.Labels = mergeLabels(cur.Labels)
	_, err = api.Update(ctx, cur, metav1.UpdateOptions{})
	return err
}

func ensureServiceAccount(ctx context.Context, cs kubernetes.Interface, st State) error {
	api := cs.CoreV1().ServiceAccounts(Namespace)
	if st == Absent {
		_, err := api.Create(ctx, &corev1.ServiceAccount{ObjectMeta: metav1.ObjectMeta{Name: Name, Namespace: Namespace, Labels: labels()}}, metav1.CreateOptions{})
		return err
	}
	cur, err := api.Get(ctx, Name, metav1.GetOptions{})
	if err != nil {
		return err
	}
	cur.Labels = mergeLabels(cur.Labels)
	_, err = api.Update(ctx, cur, metav1.UpdateOptions{})
	return err
}

func ensureClusterRole(ctx context.Context, cs kubernetes.Interface, st State) error {
	api := cs.RbacV1().ClusterRoles()
	if st == Absent {
		_, err := api.Create(ctx, &rbacv1.ClusterRole{ObjectMeta: metav1.ObjectMeta{Name: Name, Labels: labels()}, Rules: desiredRules()}, metav1.CreateOptions{})
		return err
	}
	cur, err := api.Get(ctx, Name, metav1.GetOptions{})
	if err != nil {
		return err
	}
	cur.Labels = mergeLabels(cur.Labels)
	cur.Rules = desiredRules()
	cur.AggregationRule = nil
	_, err = api.Update(ctx, cur, metav1.UpdateOptions{})
	return err
}

func ensureBinding(ctx context.Context, cs kubernetes.Interface, st State) error {
	api := cs.RbacV1().ClusterRoleBindings()
	desired := &rbacv1.ClusterRoleBinding{
		ObjectMeta: metav1.ObjectMeta{Name: Name, Labels: labels()},
		RoleRef:    desiredRoleRef(),
		Subjects:   []rbacv1.Subject{desiredSubject()},
	}
	if st == Absent {
		_, err := api.Create(ctx, desired, metav1.CreateOptions{})
		return err
	}
	cur, err := api.Get(ctx, Name, metav1.GetOptions{})
	if err != nil {
		return err
	}
	if cur.RoleRef != desiredRoleRef() {
		// roleRef is immutable in Kubernetes: a binding to a different role must be replaced.
		if err := api.Delete(ctx, Name, metav1.DeleteOptions{}); err != nil {
			return err
		}
		_, err := api.Create(ctx, desired, metav1.CreateOptions{})
		return err
	}
	cur.Labels = mergeLabels(cur.Labels)
	cur.Subjects = desired.Subjects
	_, err = api.Update(ctx, cur, metav1.UpdateOptions{})
	return err
}

func mergeLabels(have map[string]string) map[string]string {
	out := map[string]string{}
	for k, v := range have {
		out[k] = v
	}
	out[ManagedByKey] = ManagedByValue
	return out
}

// RevokePlan is exactly what Revoke will do: Delete is what is removed, Keep is what is deliberately
// left alone and why. The approval dialog shows both, and Revoke executes only Delete.
type RevokePlan struct {
	Delete []Change
	Keep   []string
}

// PlanRevoke decides, failing closed, what may be deleted:
//   - the ClusterRoleBinding, ClusterRole and ServiceAccount, unless another tool has labelled them
//     as its own;
//   - the Namespace ONLY if it carries this extension's ownership label (so it is provably ours) and
//     holds nothing else. A namespace created by the old setup script has no label and is kept. If the
//     contents cannot be listed, the namespace is kept. Deleting a namespace deletes everything in it,
//     so doubt always means keep.
func PlanRevoke(ctx context.Context, cs kubernetes.Interface, r Report) RevokePlan {
	var plan RevokePlan
	have := map[Kind]ObjectState{}
	for _, o := range r.Objects {
		have[o.Kind] = o
	}
	for _, k := range []Kind{KindClusterRoleBinding, KindClusterRole, KindServiceAccount} {
		o, ok := have[k]
		if !ok || o.State == Absent {
			continue
		}
		if o.foreign() {
			plan.Keep = append(plan.Keep, fmt.Sprintf("%s %s kept: it is managed by %q, not by this extension", k, o.Name, o.Owner))
			continue
		}
		plan.Delete = append(plan.Delete, Change{Action: "delete", Kind: k, Name: o.Name})
	}

	ns, ok := have[KindNamespace]
	if !ok || ns.State == Absent {
		return plan
	}
	switch {
	case ns.foreign():
		plan.Keep = append(plan.Keep, fmt.Sprintf("Namespace %s kept: it is managed by %q, not by this extension", Namespace, ns.Owner))
	case ns.Owner != ManagedByValue:
		plan.Keep = append(plan.Keep, fmt.Sprintf("Namespace %s kept: it was not created by this extension (no ownership label); delete it yourself if you want it gone", Namespace))
	default:
		if reasons := namespaceHolds(ctx, cs); len(reasons) > 0 {
			plan.Keep = append(plan.Keep, fmt.Sprintf("Namespace %s kept: %s", Namespace, strings.Join(reasons, "; ")))
		} else {
			plan.Delete = append(plan.Delete, Change{Action: "delete", Kind: KindNamespace, Name: Namespace})
		}
	}
	return plan
}

// namespaceHolds lists reasons the namespace must not be deleted. Anything that cannot be listed
// counts as a reason, so a permission error never leads to a deletion.
func namespaceHolds(ctx context.Context, cs kubernetes.Interface) []string {
	var reasons []string
	note := func(what string, n int, err error) {
		switch {
		case err != nil:
			reasons = append(reasons, "could not verify it is empty ("+what+" could not be listed)")
		case n > 0:
			reasons = append(reasons, fmt.Sprintf("it contains %d %s", n, what))
		}
	}
	opts := metav1.ListOptions{}
	core, apps, batch, net, rb := cs.CoreV1(), cs.AppsV1(), cs.BatchV1(), cs.NetworkingV1(), cs.RbacV1()

	sas, err := core.ServiceAccounts(Namespace).List(ctx, opts)
	if err == nil {
		n := 0
		for _, sa := range sas.Items {
			if sa.Name != "default" && sa.Name != Name {
				n++
			}
		}
		note("other service accounts", n, nil)
	} else {
		note("service accounts", 0, err)
	}
	if l, err := core.Pods(Namespace).List(ctx, opts); err != nil {
		note("pods", 0, err)
	} else {
		note("pods", len(l.Items), nil)
	}
	if l, err := core.Services(Namespace).List(ctx, opts); err != nil {
		note("services", 0, err)
	} else {
		note("services", len(l.Items), nil)
	}
	if l, err := core.Secrets(Namespace).List(ctx, opts); err != nil {
		note("secrets", 0, err)
	} else {
		note("secrets", len(l.Items), nil)
	}
	if l, err := core.ConfigMaps(Namespace).List(ctx, opts); err != nil {
		note("config maps", 0, err)
	} else {
		n := 0
		for _, cm := range l.Items {
			if cm.Name != "kube-root-ca.crt" { // Kubernetes puts this in every namespace itself
				n++
			}
		}
		note("config maps", n, nil)
	}
	if l, err := core.PersistentVolumeClaims(Namespace).List(ctx, opts); err != nil {
		note("persistent volume claims", 0, err)
	} else {
		note("persistent volume claims", len(l.Items), nil)
	}
	if l, err := apps.Deployments(Namespace).List(ctx, opts); err != nil {
		note("deployments", 0, err)
	} else {
		note("deployments", len(l.Items), nil)
	}
	if l, err := apps.StatefulSets(Namespace).List(ctx, opts); err != nil {
		note("stateful sets", 0, err)
	} else {
		note("stateful sets", len(l.Items), nil)
	}
	if l, err := apps.DaemonSets(Namespace).List(ctx, opts); err != nil {
		note("daemon sets", 0, err)
	} else {
		note("daemon sets", len(l.Items), nil)
	}
	if l, err := batch.Jobs(Namespace).List(ctx, opts); err != nil {
		note("jobs", 0, err)
	} else {
		note("jobs", len(l.Items), nil)
	}
	if l, err := batch.CronJobs(Namespace).List(ctx, opts); err != nil {
		note("cron jobs", 0, err)
	} else {
		note("cron jobs", len(l.Items), nil)
	}
	if l, err := net.Ingresses(Namespace).List(ctx, opts); err != nil {
		note("ingresses", 0, err)
	} else {
		note("ingresses", len(l.Items), nil)
	}
	if l, err := rb.Roles(Namespace).List(ctx, opts); err != nil {
		note("roles", 0, err)
	} else {
		note("roles", len(l.Items), nil)
	}
	if l, err := rb.RoleBindings(Namespace).List(ctx, opts); err != nil {
		note("role bindings", 0, err)
	} else {
		note("role bindings", len(l.Items), nil)
	}
	return reasons
}

// Revoke deletes exactly the objects in plan.Delete. Every token already issued stops working once
// its service account is gone. Objects already gone are fine, so it is safe to repeat.
func Revoke(ctx context.Context, cs kubernetes.Interface, plan RevokePlan) error {
	ignore := func(e error) error {
		if e == nil || apierrors.IsNotFound(e) {
			return nil
		}
		return wrap(e)
	}
	for _, c := range plan.Delete {
		var err error
		switch c.Kind {
		case KindClusterRoleBinding:
			err = cs.RbacV1().ClusterRoleBindings().Delete(ctx, c.Name, metav1.DeleteOptions{})
		case KindClusterRole:
			err = cs.RbacV1().ClusterRoles().Delete(ctx, c.Name, metav1.DeleteOptions{})
		case KindServiceAccount:
			err = cs.CoreV1().ServiceAccounts(Namespace).Delete(ctx, c.Name, metav1.DeleteOptions{})
		case KindNamespace:
			err = cs.CoreV1().Namespaces().Delete(ctx, c.Name, metav1.DeleteOptions{})
		}
		if e := ignore(err); e != nil {
			return e
		}
	}
	return nil
}
