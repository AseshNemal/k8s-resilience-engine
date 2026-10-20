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

	"resilience-engine/helper/internal/kube"
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
	// Outdated marks a role that grants only part of what this version needs and nothing beyond it,
	// typically one approved by an earlier version. It is not tampering, so it is described differently.
	Outdated bool `json:"outdated,omitempty"`
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

func desiredRules() []kube.PolicyRule {
	return []kube.PolicyRule{
		{APIGroups: []string{""}, Resources: []string{"nodes", "pods", "services"}, Verbs: []string{"get", "list"}},
		{APIGroups: []string{"apps"}, Resources: []string{"deployments", "replicasets"}, Verbs: []string{"get", "list"}},
	}
}

func desiredSubject() kube.Subject {
	return kube.Subject{Kind: kube.ServiceAccountKind, Name: Name, Namespace: Namespace}
}

func desiredRoleRef() kube.RoleRef {
	return kube.RoleRef{APIGroup: kube.RBACGroup, Kind: "ClusterRole", Name: Name}
}

// Permissions describes in plain words what the credential can do, for the approval dialog.
func Permissions() []string {
	return []string{
		"Read (get, list) nodes, pods and services",
		"Read (get, list) deployments and replicasets",
		"No access to Secrets, ConfigMaps or any other resource; no create, update, patch or delete",
	}
}

// normalizeRules makes rule comparison independent of ordering inside each rule.
func normalizeRules(in []kube.PolicyRule) []kube.PolicyRule {
	out := make([]kube.PolicyRule, 0, len(in))
	for _, r := range in {
		c := kube.PolicyRule{
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
func Inspect(ctx context.Context, cs kube.Client) (Report, error) {
	var r Report

	ns, err := cs.Get(ctx, kube.Namespaces, "", Namespace)
	r.add(KindNamespace, Namespace, err, func() string { return ns.Labels[ManagedByKey] }, func() (State, string) { return labelState(ns.Labels) })
	if err != nil && !kube.IsNotFound(err) {
		return r, wrap(err)
	}

	sa, err := cs.Get(ctx, kube.ServiceAccounts, Namespace, Name)
	r.add(KindServiceAccount, Name, err, func() string { return sa.Labels[ManagedByKey] }, func() (State, string) { return labelState(sa.Labels) })
	if err != nil && !kube.IsNotFound(err) {
		return r, wrap(err)
	}

	cr, err := cs.Get(ctx, kube.ClusterRoles, "", Name)
	r.add(KindClusterRole, Name, err, func() string { return cr.Labels[ManagedByKey] }, func() (State, string) {
		if cr.AggregationRule {
			return Drifted, "has an aggregation rule, so it can gain permissions from other roles"
		}
		if !reflect.DeepEqual(normalizeRules(cr.Rules), normalizeRules(desiredRules())) {
			return Drifted, "rules differ from the approved read-only set"
		}
		return labelState(cr.Labels)
	})
	if err != nil && !kube.IsNotFound(err) {
		return r, wrap(err)
	}
	if err == nil {
		r.markOutdated(cr)
	}

	crb, err := cs.Get(ctx, kube.ClusterRoleBindings, "", Name)
	r.add(KindClusterRoleBinding, Name, err, func() string { return crb.Labels[ManagedByKey] }, func() (State, string) {
		if crb.RoleRef != desiredRoleRef() {
			return Drifted, "binds a different role"
		}
		if len(crb.Subjects) != 1 || crb.Subjects[0] != desiredSubject() {
			return Drifted, "binds subjects other than the extension's service account"
		}
		return labelState(crb.Labels)
	})
	if err != nil && !kube.IsNotFound(err) {
		return r, wrap(err)
	}

	extras, err := extraGrants(ctx, cs)
	if err != nil {
		return r, wrap(err)
	}
	r.ExtraGrants = extras
	return r, nil
}

// ruleTriples flattens rules into "group/resource/verb". ok is false when a rule restricts by resource
// name or non-resource URL, which this comparison does not model.
func ruleTriples(rules []kube.PolicyRule) (set map[string]bool, ok bool) {
	set = map[string]bool{}
	for _, rule := range rules {
		if len(rule.ResourceNames) > 0 || len(rule.NonResourceURLs) > 0 {
			return nil, false
		}
		for _, group := range rule.APIGroups {
			for _, resource := range rule.Resources {
				for _, verb := range rule.Verbs {
					set[group+"/"+resource+"/"+verb] = true
				}
			}
		}
	}
	return set, true
}

// markOutdated tells a role that is merely narrower than the approved set (every grant it has is
// still wanted) from one that was changed or widened. Only the first is a normal upgrade.
func (r *Report) markOutdated(cr *kube.Object) {
	have, okHave := ruleTriples(cr.Rules)
	want, _ := ruleTriples(desiredRules())
	if cr.AggregationRule || !okHave || len(have) == 0 {
		return
	}
	for triple := range have {
		if !want[triple] {
			return
		}
	}
	haveResources := map[string]bool{}
	for triple := range have {
		haveResources[triple[:strings.LastIndex(triple, "/")]] = true
	}
	var missing []string
	for triple := range want {
		key := triple[:strings.LastIndex(triple, "/")]
		if !haveResources[key] {
			missing = append(missing, key[strings.LastIndex(key, "/")+1:])
			haveResources[key] = true // report each resource once
		}
	}
	sort.Strings(missing)
	what := "more read access"
	if len(missing) > 0 {
		what = "read access to " + strings.Join(missing, ", ")
	}
	for i := range r.Objects {
		if o := &r.Objects[i]; o.Kind == KindClusterRole && o.State == Drifted {
			o.Outdated = true
			o.Detail = "this version of the extension also needs " + what
		}
	}
}

// NeedsMoreAccess reports that the only difference from the approved set is a role that predates it:
// nothing was widened, changed or added by anyone else.
func (r Report) NeedsMoreAccess() bool {
	if len(r.ExtraGrants) > 0 {
		return false
	}
	found := false
	for _, o := range r.Objects {
		switch {
		case o.State == Drifted && o.Kind == KindClusterRole && o.Outdated:
			found = true
		case o.State == Drifted:
			return false
		}
	}
	return found
}

func (r *Report) add(kind Kind, name string, err error, owner func() string, classify func() (State, string)) {
	if err != nil {
		if kube.IsNotFound(err) {
			r.Objects = append(r.Objects, ObjectState{Kind: kind, Name: name, State: Absent})
		}
		return
	}
	state, detail := classify()
	r.Objects = append(r.Objects, ObjectState{Kind: kind, Name: name, State: state, Detail: detail, Owner: owner()})
}

// extraGrants finds bindings, other than the extension's own, that name its service account.
func extraGrants(ctx context.Context, cs kube.Client) ([]ExtraGrant, error) {
	var out []ExtraGrant
	hit := func(subjects []kube.Subject) bool {
		for _, s := range subjects {
			if s.Kind == kube.ServiceAccountKind && s.Name == Name && s.Namespace == Namespace {
				return true
			}
		}
		return false
	}
	crbs, err := cs.List(ctx, kube.ClusterRoleBindings, "")
	if err != nil {
		return nil, err
	}
	for _, b := range crbs {
		if b.Name != Name && hit(b.Subjects) {
			out = append(out, ExtraGrant{Kind: "ClusterRoleBinding", Name: b.Name})
		}
	}
	rbs, err := cs.List(ctx, kube.RoleBindings, "") // every namespace
	if err != nil {
		return nil, err
	}
	for _, b := range rbs {
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
			suffix := "; will be reset to the approved read-only set"
			if o.Outdated {
				suffix = "; the missing read access will be added"
			}
			out = append(out, Change{Action: "update", Kind: o.Kind, Name: o.Name, Detail: o.Detail + suffix})
		}
	}
	return out
}

func wrap(err error) error {
	if kube.IsForbidden(err) {
		return fmt.Errorf("%w: %s", ErrForbidden, kube.ReasonForError(err))
	}
	return err
}

// Apply performs a Plan. It refuses to run when another binding over-grants the service account.
func Apply(ctx context.Context, cs kube.Client, r Report) error {
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

func ensureNamespace(ctx context.Context, cs kube.Client, st State) error {
	if st == Absent {
		return cs.Create(ctx, kube.Namespaces, &kube.Object{Name: Namespace, Labels: labels()})
	}
	cur, err := cs.Get(ctx, kube.Namespaces, "", Namespace)
	if err != nil {
		return err
	}
	cur.Labels = mergeLabels(cur.Labels)
	return cs.Update(ctx, kube.Namespaces, cur)
}

func ensureServiceAccount(ctx context.Context, cs kube.Client, st State) error {
	if st == Absent {
		return cs.Create(ctx, kube.ServiceAccounts, &kube.Object{Name: Name, Namespace: Namespace, Labels: labels()})
	}
	cur, err := cs.Get(ctx, kube.ServiceAccounts, Namespace, Name)
	if err != nil {
		return err
	}
	cur.Labels = mergeLabels(cur.Labels)
	return cs.Update(ctx, kube.ServiceAccounts, cur)
}

func ensureClusterRole(ctx context.Context, cs kube.Client, st State) error {
	if st == Absent {
		return cs.Create(ctx, kube.ClusterRoles, &kube.Object{Name: Name, Labels: labels(), Rules: desiredRules()})
	}
	cur, err := cs.Get(ctx, kube.ClusterRoles, "", Name)
	if err != nil {
		return err
	}
	cur.Labels = mergeLabels(cur.Labels)
	cur.Rules = desiredRules()
	cur.AggregationRule = false
	return cs.Update(ctx, kube.ClusterRoles, cur)
}

func ensureBinding(ctx context.Context, cs kube.Client, st State) error {
	desired := &kube.Object{
		Name:     Name,
		Labels:   labels(),
		RoleRef:  desiredRoleRef(),
		Subjects: []kube.Subject{desiredSubject()},
	}
	if st == Absent {
		return cs.Create(ctx, kube.ClusterRoleBindings, desired)
	}
	cur, err := cs.Get(ctx, kube.ClusterRoleBindings, "", Name)
	if err != nil {
		return err
	}
	if cur.RoleRef != desiredRoleRef() {
		// roleRef is immutable in Kubernetes: a binding to a different role must be replaced.
		if err := cs.Delete(ctx, kube.ClusterRoleBindings, "", Name); err != nil {
			return err
		}
		return cs.Create(ctx, kube.ClusterRoleBindings, desired)
	}
	cur.Labels = mergeLabels(cur.Labels)
	cur.Subjects = desired.Subjects
	return cs.Update(ctx, kube.ClusterRoleBindings, cur)
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
func PlanRevoke(ctx context.Context, cs kube.Client, r Report) RevokePlan {
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
func namespaceHolds(ctx context.Context, cs kube.Client) []string {
	var reasons []string
	note := func(what string, n int, err error) {
		switch {
		case err != nil:
			reasons = append(reasons, "could not verify it is empty ("+what+" could not be listed)")
		case n > 0:
			reasons = append(reasons, fmt.Sprintf("it contains %d %s", n, what))
		}
	}
	// count lists one kind in the namespace, ignoring the objects Kubernetes or this extension create
	// themselves. A kind that cannot be listed counts as a reason to keep the namespace.
	count := func(r kube.Resource, what string, own ...string) {
		items, err := cs.List(ctx, r, Namespace)
		if err != nil {
			note(strings.TrimPrefix(what, "other "), 0, err) // "service accounts could not be listed"
			return
		}
		n := 0
	next:
		for _, item := range items {
			for _, name := range own {
				if item.Name == name {
					continue next
				}
			}
			n++
		}
		note(what, n, nil)
	}

	count(kube.ServiceAccounts, "other service accounts", "default", Name)
	count(kube.Pods, "pods")
	count(kube.Services, "services")
	count(kube.Secrets, "secrets")
	count(kube.ConfigMaps, "config maps", "kube-root-ca.crt") // Kubernetes puts this in every namespace itself
	count(kube.PersistentVolumeClaims, "persistent volume claims")
	count(kube.Deployments, "deployments")
	count(kube.StatefulSets, "stateful sets")
	count(kube.DaemonSets, "daemon sets")
	count(kube.Jobs, "jobs")
	count(kube.CronJobs, "cron jobs")
	count(kube.Ingresses, "ingresses")
	count(kube.Roles, "roles")
	count(kube.RoleBindings, "role bindings")
	return reasons
}

// Revoke deletes exactly the objects in plan.Delete. Every token already issued stops working once
// its service account is gone. Objects already gone are fine, so it is safe to repeat.
func Revoke(ctx context.Context, cs kube.Client, plan RevokePlan) error {
	ignore := func(e error) error {
		if e == nil || kube.IsNotFound(e) {
			return nil
		}
		return wrap(e)
	}
	for _, c := range plan.Delete {
		var err error
		switch c.Kind {
		case KindClusterRoleBinding:
			err = cs.Delete(ctx, kube.ClusterRoleBindings, "", c.Name)
		case KindClusterRole:
			err = cs.Delete(ctx, kube.ClusterRoles, "", c.Name)
		case KindServiceAccount:
			err = cs.Delete(ctx, kube.ServiceAccounts, Namespace, c.Name)
		case KindNamespace:
			err = cs.Delete(ctx, kube.Namespaces, "", c.Name)
		}
		if e := ignore(err); e != nil {
			return e
		}
	}
	return nil
}
