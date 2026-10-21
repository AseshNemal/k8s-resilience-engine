package rbac

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"strings"
	"testing"

	"go.yaml.in/yaml/v3"

	"resilience-engine/helper/internal/kube"
	"resilience-engine/helper/internal/kube/kubetest"
)

var ctx = context.Background()

// ---------------------------------------------------------------- builders for cluster state

// seeded is an object together with the collection it lives in, so a test can put it in a fake cluster.
type seeded struct {
	res kube.Resource
	obj *kube.Object
}

func goodObjects(labelled bool) []seeded {
	lab := labels()
	if !labelled {
		lab = nil
	}
	return []seeded{
		{kube.Namespaces, &kube.Object{Name: Namespace, Labels: lab}},
		{kube.ServiceAccounts, &kube.Object{Name: Name, Namespace: Namespace, Labels: lab}},
		{kube.ClusterRoles, &kube.Object{Name: Name, Labels: lab, Rules: desiredRules()}},
		{kube.ClusterRoleBindings, &kube.Object{Name: Name, Labels: lab, RoleRef: desiredRoleRef(), Subjects: []kube.Subject{desiredSubject()}}},
	}
}

func cluster(objs ...seeded) *kubetest.Fake {
	cs := kubetest.New()
	for _, s := range objs {
		cs.Put(s.res, *s.obj)
	}
	return cs
}

func plusOthers(objs []seeded, others ...seeded) []seeded {
	return append(append([]seeded{}, objs...), others...)
}

func forbiddenErr() error { return kube.NewAPIError(403, "Forbidden", "x") }

func states(r Report) map[Kind]State {
	m := map[Kind]State{}
	for _, o := range r.Objects {
		m[o.Kind] = o.State
	}
	return m
}

func inspect(t *testing.T, cs kube.Client) Report {
	t.Helper()
	r, err := Inspect(ctx, cs)
	if err != nil {
		t.Fatalf("Inspect: %v", err)
	}
	return r
}

// ---------------------------------------------------------------- classification

func TestEmptyClusterIsAbsentAndPlansFourCreates(t *testing.T) {
	cs := kubetest.New()
	r := inspect(t, cs)

	if r.Overall() != OverallAbsent || r.Usable() {
		t.Fatalf("overall = %s usable = %v", r.Overall(), r.Usable())
	}
	plan := Plan(r)
	if len(plan) != 4 {
		t.Fatalf("plan = %v", plan)
	}
	for _, c := range plan {
		if c.Action != "create" {
			t.Fatalf("unexpected action %v", c)
		}
	}
	if w, _ := cs.Writes(); len(w) != 0 {
		t.Fatalf("Inspect must not write, wrote %v", w)
	}
}

func TestExactlyApprovedAccessIsUsableAndNeedsNoChange(t *testing.T) {
	r := inspect(t, cluster(goodObjects(true)...))

	if r.Overall() != OverallApproved || !r.Usable() || len(Plan(r)) != 0 {
		t.Fatalf("overall = %s, plan = %v", r.Overall(), Plan(r))
	}
}

func TestAccessMadeByTheOldScriptHasNoLabelButIsStillSafeToRenew(t *testing.T) {
	r := inspect(t, cluster(goodObjects(false)...))

	if r.Overall() != OverallApprovedOld || !r.Usable() {
		t.Fatalf("overall = %s usable = %v", r.Overall(), r.Usable())
	}
	plan := Plan(r)
	if len(plan) != 4 {
		t.Fatalf("plan = %v", plan)
	}
	for _, c := range plan {
		if c.Action != "label" {
			t.Fatalf("a missing label must plan a label change only, got %v", c)
		}
	}
}

func TestPartialAccessIsNotUsable(t *testing.T) {
	objs := goodObjects(true)
	r := inspect(t, cluster(objs[0], objs[1])) // namespace and service account only

	if r.Overall() != OverallPartial || r.Usable() {
		t.Fatalf("overall = %s usable = %v", r.Overall(), r.Usable())
	}
}

func TestRuleOrderDoesNotMatterButContentDoes(t *testing.T) {
	objs := goodObjects(true)
	objs[2].obj.Rules = []kube.PolicyRule{ // same rules, reversed order everywhere
		{APIGroups: []string{"apps"}, Resources: []string{"replicasets", "deployments"}, Verbs: []string{"list", "get"}},
		{APIGroups: []string{""}, Resources: []string{"services", "pods", "nodes"}, Verbs: []string{"list", "get"}},
	}
	if r := inspect(t, cluster(objs...)); r.Overall() != OverallApproved {
		t.Fatalf("reordered rules should still match, got %s", r.Overall())
	}
}

func TestWidenedClusterRoleIsDriftedAndNotUsable(t *testing.T) {
	cases := map[string]func(cr *kube.Object){
		"reads secrets": func(cr *kube.Object) {
			cr.Rules = append(cr.Rules, kube.PolicyRule{APIGroups: []string{""}, Resources: []string{"secrets"}, Verbs: []string{"get"}})
		},
		"adds a write verb": func(cr *kube.Object) { cr.Rules[0].Verbs = append(cr.Rules[0].Verbs, "delete") },
		"wildcard": func(cr *kube.Object) {
			cr.Rules = []kube.PolicyRule{{APIGroups: []string{"*"}, Resources: []string{"*"}, Verbs: []string{"*"}}}
		},
		"drops a rule":    func(cr *kube.Object) { cr.Rules = cr.Rules[:1] },
		"aggregation":     func(cr *kube.Object) { cr.AggregationRule = true },
		"restricted name": func(cr *kube.Object) { cr.Rules[0].ResourceNames = []string{"only-this"} },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			objs := goodObjects(true)
			mutate(objs[2].obj)
			r := inspect(t, cluster(objs...))
			if r.Overall() != OverallDrifted || r.Usable() {
				t.Fatalf("overall = %s usable = %v", r.Overall(), r.Usable())
			}
			if states(r)[KindClusterRole] != Drifted {
				t.Fatalf("ClusterRole state = %s", states(r)[KindClusterRole])
			}
		})
	}
}

func TestBindingToAnotherRoleOrExtraSubjectsIsDrifted(t *testing.T) {
	t.Run("different role", func(t *testing.T) {
		objs := goodObjects(true)
		objs[3].obj.RoleRef = kube.RoleRef{APIGroup: kube.RBACGroup, Kind: "ClusterRole", Name: "cluster-admin"}
		if r := inspect(t, cluster(objs...)); r.Overall() != OverallDrifted || r.Usable() {
			t.Fatalf("overall = %s", r.Overall())
		}
	})
	t.Run("extra subject", func(t *testing.T) {
		objs := goodObjects(true)
		objs[3].obj.Subjects = append(objs[3].obj.Subjects, kube.Subject{Kind: "User", Name: "mallory", APIGroup: kube.RBACGroup})
		if r := inspect(t, cluster(objs...)); r.Overall() != OverallDrifted || r.Usable() {
			t.Fatalf("overall = %s", r.Overall())
		}
	})
}

func TestSomeoneElsesLabelIsDrift(t *testing.T) {
	objs := goodObjects(true)
	objs[2].obj.Labels = map[string]string{ManagedByKey: "helm"}
	if r := inspect(t, cluster(objs...)); r.Overall() != OverallDrifted {
		t.Fatalf("overall = %s", r.Overall())
	}
}

func TestAnotherBindingThatGrantsTheServiceAccountMoreMakesItOverGranted(t *testing.T) {
	adminCRB := seeded{kube.ClusterRoleBindings, &kube.Object{
		Name:     "grant-admin",
		RoleRef:  kube.RoleRef{APIGroup: kube.RBACGroup, Kind: "ClusterRole", Name: "cluster-admin"},
		Subjects: []kube.Subject{desiredSubject()},
	}}
	nsRB := seeded{kube.RoleBindings, &kube.Object{
		Name: "edit-default", Namespace: "default",
		RoleRef:  kube.RoleRef{APIGroup: kube.RBACGroup, Kind: "ClusterRole", Name: "edit"},
		Subjects: []kube.Subject{desiredSubject()},
	}}
	for name, extra := range map[string]seeded{"cluster binding": adminCRB, "namespaced binding": nsRB} {
		t.Run(name, func(t *testing.T) {
			cs := cluster(plusOthers(goodObjects(true), extra)...)
			r := inspect(t, cs)
			if r.Overall() != OverallOverGranted || r.Usable() || len(r.ExtraGrants) != 1 {
				t.Fatalf("overall = %s grants = %v", r.Overall(), r.ExtraGrants)
			}
			cs.ClearActions()
			if err := Apply(ctx, cs, r); err != ErrOverGranted {
				t.Fatalf("Apply = %v, want ErrOverGranted", err)
			}
			if w, _ := cs.Writes(); len(w) != 0 {
				t.Fatalf("a refused Apply must not write, wrote %v", w)
			}
		})
	}
}

func TestOurOwnBindingIsNotCountedAsAnExtraGrant(t *testing.T) {
	if r := inspect(t, cluster(goodObjects(true)...)); len(r.ExtraGrants) != 0 {
		t.Fatalf("grants = %v", r.ExtraGrants)
	}
}

// ---------------------------------------------------------------- apply

func TestApplyCreatesEverythingLabelledAndTheResultInspectsAsApproved(t *testing.T) {
	cs := kubetest.New()
	if err := Apply(ctx, cs, inspect(t, cs)); err != nil {
		t.Fatal(err)
	}
	cs.ClearActions()
	r := inspect(t, cs)
	if r.Overall() != OverallApproved || len(Plan(r)) != 0 {
		t.Fatalf("overall = %s plan = %v", r.Overall(), Plan(r))
	}
	cr, _ := cs.Lookup(kube.ClusterRoles, "", Name)
	if cr.Labels[ManagedByKey] != ManagedByValue {
		t.Fatal("created objects must carry the ownership label")
	}
}

func TestApplyRepairsDriftAndTakesNothingOutsideItsOwnFourObjects(t *testing.T) {
	objs := goodObjects(true)
	objs[2].obj.Rules = append(objs[2].obj.Rules, kube.PolicyRule{APIGroups: []string{""}, Resources: []string{"secrets"}, Verbs: []string{"get"}})
	objs[3].obj.RoleRef = kube.RoleRef{APIGroup: kube.RBACGroup, Kind: "ClusterRole", Name: "cluster-admin"}
	other := seeded{kube.ClusterRoles, &kube.Object{Name: "someone-elses"}}
	cs := cluster(plusOthers(objs, other)...)

	if err := Apply(ctx, cs, inspect(t, cs)); err != nil {
		t.Fatal(err)
	}
	if r := inspect(t, cs); r.Overall() != OverallApproved {
		t.Fatalf("after repair overall = %s", r.Overall())
	}
	if _, ok := cs.Lookup(kube.ClusterRoles, "", "someone-elses"); !ok {
		t.Fatal("unrelated object was touched")
	}
	for _, a := range cs.Actions() {
		if a.Name == "someone-elses" && a.Verb != "list" {
			t.Fatalf("unrelated object was acted on: %+v", a)
		}
	}
}

func TestApplyReplacesABindingToAnotherRoleBecauseARoleRefCannotBeChanged(t *testing.T) {
	objs := goodObjects(true)
	objs[3].obj.RoleRef = kube.RoleRef{APIGroup: kube.RBACGroup, Kind: "ClusterRole", Name: "cluster-admin"}
	cs := cluster(objs...)
	r := inspect(t, cs)
	cs.ClearActions()

	if err := Apply(ctx, cs, r); err != nil {
		t.Fatal(err)
	}
	w, _ := cs.Writes()
	// The role itself is fine and must not be touched: only the binding is deleted and recreated.
	if strings.Join(w, ",") != "delete clusterrolebindings,create clusterrolebindings" {
		t.Fatalf("writes = %v", w)
	}
	crb, _ := cs.Lookup(kube.ClusterRoleBindings, "", Name)
	if crb.RoleRef != desiredRoleRef() {
		t.Fatalf("roleRef = %+v", crb.RoleRef)
	}
}

func TestApplyOnlyLabelsLegacyObjectsAndLeavesContentAlone(t *testing.T) {
	cs := cluster(goodObjects(false)...)
	cs.ClearActions()
	if err := Apply(ctx, cs, inspect(t, cs)); err != nil {
		t.Fatal(err)
	}
	w, _ := cs.Writes()
	if len(w) != 4 {
		t.Fatalf("writes = %v", w)
	}
	for _, line := range w {
		if !strings.HasPrefix(line, "update ") {
			t.Fatalf("legacy labelling must only update, did %q", line)
		}
	}
	if r := inspect(t, cs); r.Overall() != OverallApproved {
		t.Fatalf("overall = %s", r.Overall())
	}
}

func TestApplyOnAnAlreadyApprovedClusterWritesNothing(t *testing.T) {
	cs := cluster(goodObjects(true)...)
	r := inspect(t, cs)
	cs.ClearActions()
	if err := Apply(ctx, cs, r); err != nil {
		t.Fatal(err)
	}
	if w, _ := cs.Writes(); len(w) != 0 {
		t.Fatalf("wrote %v", w)
	}
}

// ---------------------------------------------------------------- RBAC denial of the USER's kubeconfig

func TestApplyReportsForbiddenWhenTheUsersKubeconfigCannotManageRBAC(t *testing.T) {
	cs := kubetest.New()
	cs.Fail("create", "clusterroles", forbiddenErr())
	err := Apply(ctx, cs, inspect(t, cs))
	if err == nil || !strings.Contains(err.Error(), ErrForbidden.Error()) || !errors.Is(err, ErrForbidden) {
		t.Fatalf("err = %v", err)
	}
}

func TestInspectReportsForbiddenWhenTheUserCannotReadRBAC(t *testing.T) {
	cs := kubetest.New()
	cs.Fail("get", "clusterroles", forbiddenErr())
	if _, err := Inspect(ctx, cs); err == nil || !strings.Contains(err.Error(), ErrForbidden.Error()) {
		t.Fatalf("err = %v", err)
	}
}

func TestOtherFailuresAreNotMistakenForForbidden(t *testing.T) {
	cs := kubetest.New()
	cs.Fail("get", "clusterroles", errors.New("connection reset"))
	_, err := Inspect(ctx, cs)
	if err == nil || errors.Is(err, ErrForbidden) {
		t.Fatalf("err = %v", err)
	}
}

// ---------------------------------------------------------------- revoke (fails closed)

func planFor(t *testing.T, cs *kubetest.Fake) RevokePlan {
	t.Helper()
	return PlanRevoke(ctx, cs, inspect(t, cs))
}

func deleted(p RevokePlan) []string {
	var out []string
	for _, c := range p.Delete {
		out = append(out, string(c.Kind))
	}
	return out
}

func TestRevokeRemovesAnEmptyLabelledNamespaceAndTheAccessObjectsAndIsIdempotent(t *testing.T) {
	cs := cluster(goodObjects(true)...)
	plan := planFor(t, cs)
	if got := strings.Join(deleted(plan), ","); got != "ClusterRoleBinding,ClusterRole,ServiceAccount,Namespace" || len(plan.Keep) != 0 {
		t.Fatalf("plan = %v keep %v", got, plan.Keep)
	}
	if err := Revoke(ctx, cs, plan); err != nil {
		t.Fatal(err)
	}
	if r := inspect(t, cs); r.Overall() != OverallAbsent {
		t.Fatalf("after revoke overall = %s", r.Overall())
	}
	if err := Revoke(ctx, cs, plan); err != nil {
		t.Fatalf("repeating a revoke must be a no-op, got %v", err)
	}
}

func TestRevokeKeepsTheNamespaceWhenAnythingElseLivesInIt(t *testing.T) {
	in := func(r kube.Resource, name string) seeded {
		return seeded{r, &kube.Object{Name: name, Namespace: Namespace}}
	}
	things := map[string]seeded{
		"another service account": in(kube.ServiceAccounts, "neighbour"),
		"a pod":                   in(kube.Pods, "p"),
		"a service":               in(kube.Services, "s"),
		"a secret":                in(kube.Secrets, "db-password"),
		"a config map":            in(kube.ConfigMaps, "settings"),
		"a volume claim":          in(kube.PersistentVolumeClaims, "data"),
		"a deployment":            in(kube.Deployments, "d"),
		"a stateful set":          in(kube.StatefulSets, "db"),
		"a daemon set":            in(kube.DaemonSets, "agent"),
		"a job":                   in(kube.Jobs, "j"),
		"a cron job":              in(kube.CronJobs, "c"),
		"an ingress":              in(kube.Ingresses, "i"),
		"a role":                  in(kube.Roles, "r"),
		"a role binding":          in(kube.RoleBindings, "rb"),
	}
	for name, obj := range things {
		t.Run(name, func(t *testing.T) {
			cs := cluster(plusOthers(goodObjects(true), obj)...)
			plan := planFor(t, cs)
			if strings.Contains(strings.Join(deleted(plan), ","), "Namespace") {
				t.Fatalf("the namespace must be kept when it holds %s: %v", name, deleted(plan))
			}
			if len(plan.Keep) != 1 || !strings.Contains(plan.Keep[0], "Namespace resilience-engine kept") {
				t.Fatalf("the plan must say why: %v", plan.Keep)
			}
			if err := Revoke(ctx, cs, plan); err != nil {
				t.Fatal(err)
			}
			if _, ok := cs.Lookup(kube.Namespaces, "", Namespace); !ok {
				t.Fatalf("namespace was deleted although it holds %s", name)
			}
			if _, ok := cs.Lookup(kube.ClusterRoles, "", Name); ok {
				t.Fatal("the access objects should still be removed")
			}
		})
	}
}

func TestTheNamespaceThatKubernetesFillsInItselfDoesNotCountAsContents(t *testing.T) {
	rootCA := seeded{kube.ConfigMaps, &kube.Object{Name: "kube-root-ca.crt", Namespace: Namespace}}
	cs := cluster(plusOthers(goodObjects(true), rootCA)...)
	if got := strings.Join(deleted(planFor(t, cs)), ","); !strings.Contains(got, "Namespace") {
		t.Fatalf("kube-root-ca.crt exists in every namespace and must not block deletion, plan = %s", got)
	}
}

func TestOnlyTheDefaultAndOwnServiceAccountsAreIgnoredWhenCheckingTheNamespaceIsEmpty(t *testing.T) {
	def := seeded{kube.ServiceAccounts, &kube.Object{Name: "default", Namespace: Namespace}}
	cs := cluster(plusOthers(goodObjects(true), def)...)
	if got := strings.Join(deleted(planFor(t, cs)), ","); !strings.Contains(got, "Namespace") {
		t.Fatalf("the default service account must not block deletion, plan = %s", got)
	}
}

func TestThingsInOtherNamespacesDoNotBlockDeletingOurNamespace(t *testing.T) {
	elsewhere := seeded{kube.Pods, &kube.Object{Name: "p", Namespace: "default"}}
	cs := cluster(plusOthers(goodObjects(true), elsewhere)...)
	if got := strings.Join(deleted(planFor(t, cs)), ","); !strings.Contains(got, "Namespace") {
		t.Fatalf("a pod in another namespace must not matter, plan = %s", got)
	}
}

func TestANamespaceMadeByTheOldScriptHasNoLabelSoItIsNeverDeleted(t *testing.T) {
	cs := cluster(goodObjects(false)...) // as the old setup script left it
	plan := planFor(t, cs)
	if got := strings.Join(deleted(plan), ","); got != "ClusterRoleBinding,ClusterRole,ServiceAccount" {
		t.Fatalf("only the access objects may go, plan = %s", got)
	}
	if len(plan.Keep) != 1 || !strings.Contains(plan.Keep[0], "not created by this extension") {
		t.Fatalf("keep = %v", plan.Keep)
	}
	if err := Revoke(ctx, cs, plan); err != nil {
		t.Fatal(err)
	}
	if _, ok := cs.Lookup(kube.Namespaces, "", Namespace); !ok {
		t.Fatal("an unlabelled namespace must survive a revoke")
	}
}

func TestIfTheContentsCannotBeListedTheNamespaceIsKeptNotDeleted(t *testing.T) {
	for _, resource := range []string{
		"pods", "secrets", "configmaps", "deployments", "persistentvolumeclaims", "serviceaccounts",
		"services", "statefulsets", "daemonsets", "jobs", "cronjobs", "ingresses", "roles", "rolebindings",
	} {
		t.Run(resource, func(t *testing.T) {
			cs := cluster(goodObjects(true)...)
			report := mustReport(t, cs) // before the failure is set up: Inspect lists bindings itself
			cs.Fail("list", resource, forbiddenErr())
			plan := PlanRevoke(ctx, cs, report)
			if strings.Contains(strings.Join(deleted(plan), ","), "Namespace") {
				t.Fatalf("doubt must mean keep: could not list %s but the namespace is in the plan", resource)
			}
			if len(plan.Keep) != 1 || !strings.Contains(plan.Keep[0], "could not verify") {
				t.Fatalf("keep = %v", plan.Keep)
			}
		})
	}
}

func TestACouldNotBeListedServiceAccountsIsNamedWithoutTheWordOther(t *testing.T) {
	cs := cluster(goodObjects(true)...)
	report := mustReport(t, cs)
	cs.Fail("list", "serviceaccounts", forbiddenErr())
	plan := PlanRevoke(ctx, cs, report)
	if len(plan.Keep) != 1 || !strings.Contains(plan.Keep[0], "(service accounts could not be listed)") {
		t.Fatalf("keep = %v", plan.Keep)
	}
}

// mustReport inspects the cluster.
func mustReport(t *testing.T, cs *kubetest.Fake) Report {
	t.Helper()
	r, err := Inspect(ctx, cs)
	if err != nil {
		t.Fatalf("Inspect: %v", err)
	}
	return r
}

func TestObjectsAnotherToolHasLabelledAsItsOwnAreNeverDeleted(t *testing.T) {
	objs := goodObjects(true)
	objs[2].obj.Labels = map[string]string{ManagedByKey: "helm"}
	objs[0].obj.Labels = map[string]string{ManagedByKey: "argocd"}
	cs := cluster(objs...)
	plan := planFor(t, cs)

	if got := strings.Join(deleted(plan), ","); got != "ClusterRoleBinding,ServiceAccount" {
		t.Fatalf("only the extension's own objects may be deleted, plan = %s", got)
	}
	if len(plan.Keep) != 2 {
		t.Fatalf("keep = %v", plan.Keep)
	}
	if err := Revoke(ctx, cs, plan); err != nil {
		t.Fatal(err)
	}
	if _, ok := cs.Lookup(kube.ClusterRoles, "", Name); !ok {
		t.Fatal("helm's ClusterRole was deleted")
	}
	if _, ok := cs.Lookup(kube.Namespaces, "", Namespace); !ok {
		t.Fatal("argocd's Namespace was deleted")
	}
}

func TestRevokeExecutesExactlyThePlanAndNothingMore(t *testing.T) {
	cs := cluster(goodObjects(true)...)
	only := RevokePlan{Delete: []Change{{Action: "delete", Kind: KindClusterRoleBinding, Name: Name}}}
	if err := Revoke(ctx, cs, only); err != nil {
		t.Fatal(err)
	}
	if _, ok := cs.Lookup(kube.ClusterRoles, "", Name); !ok {
		t.Fatal("the ClusterRole was not in the plan and must remain")
	}
	if _, ok := cs.Lookup(kube.Namespaces, "", Namespace); !ok {
		t.Fatal("the Namespace was not in the plan and must remain")
	}
	if err := Revoke(ctx, cs, RevokePlan{}); err != nil {
		t.Fatalf("an empty plan must do nothing: %v", err)
	}
}

func TestAnEmptyClusterHasNothingToRevoke(t *testing.T) {
	plan := planFor(t, kubetest.New())
	if len(plan.Delete) != 0 || len(plan.Keep) != 0 {
		t.Fatalf("plan = %+v", plan)
	}
}

func TestRevokeReportsForbiddenWhenTheUsersKubeconfigCannotDelete(t *testing.T) {
	cs := cluster(goodObjects(true)...)
	plan := planFor(t, cs)
	cs.Fail("delete", "clusterrolebindings", forbiddenErr())
	if err := Revoke(ctx, cs, plan); err == nil || !strings.Contains(err.Error(), ErrForbidden.Error()) {
		t.Fatalf("err = %v", err)
	}
}

func TestRevokeStopsAtTheFirstRealFailureInsteadOfDeletingTheRest(t *testing.T) {
	cs := cluster(goodObjects(true)...)
	plan := planFor(t, cs)
	cs.Fail("delete", "clusterroles", errors.New("connection reset"))
	cs.ClearActions()

	if err := Revoke(ctx, cs, plan); err == nil {
		t.Fatal("want an error")
	}
	w, _ := cs.Writes()
	if strings.Join(w, ",") != "delete clusterrolebindings,delete clusterroles" {
		t.Fatalf("writes after the failure: %v", w)
	}
	if _, ok := cs.Lookup(kube.Namespaces, "", Namespace); !ok {
		t.Fatal("nothing after the failed deletion may run")
	}
}

// ---------------------------------------------------------------- the granted resources

func TestRolePermitsExactlyTheFiveReadResources(t *testing.T) {
	got := map[string]bool{}
	for _, rule := range desiredRules() {
		for _, verb := range rule.Verbs {
			if verb != "get" && verb != "list" {
				t.Errorf("verb %q is not read-only", verb)
			}
		}
		for _, group := range rule.APIGroups {
			for _, resource := range rule.Resources {
				got[group+"/"+resource] = true
			}
		}
	}
	want := map[string]bool{
		"/nodes": true, "/pods": true, "/services": true,
		"apps/deployments": true, "apps/replicasets": true,
	}
	if len(got) != len(want) {
		t.Fatalf("resources = %v, want exactly %v", got, want)
	}
	for resource := range want {
		if !got[resource] {
			t.Errorf("missing %s in %v", resource, got)
		}
	}
}

func TestRolesApprovedBeforeServicesAndReplicasetsAreDriftedAndRepairedByOneApproval(t *testing.T) {
	previous := []kube.PolicyRule{
		{APIGroups: []string{""}, Resources: []string{"nodes", "pods"}, Verbs: []string{"get", "list"}},
		{APIGroups: []string{"apps"}, Resources: []string{"deployments"}, Verbs: []string{"get", "list"}},
	}
	objs := goodObjects(true)
	objs[2].obj.Rules = previous
	cs := cluster(objs...)

	r := inspect(t, cs)
	if r.Overall() != OverallDrifted || r.Usable() {
		t.Fatalf("overall = %s usable = %v; an old grant must stop renewal until re-approved", r.Overall(), r.Usable())
	}
	var updatesRole bool
	for _, c := range Plan(r) {
		if c.Action == "update" && c.Kind == KindClusterRole && c.Name == Name {
			updatesRole = true
		} else {
			t.Errorf("unexpected change in the plan: %s", c)
		}
	}
	if !updatesRole {
		t.Fatalf("the plan does not update the ClusterRole: %+v", Plan(r))
	}

	if err := Apply(ctx, cs, r); err != nil {
		t.Fatalf("Apply: %v", err)
	}
	if after := inspect(t, cs); after.Overall() != OverallApproved || !after.Usable() {
		t.Fatalf("after one approval: overall = %s usable = %v", after.Overall(), after.Usable())
	}
}

func TestAnOlderNarrowerRoleIsExplainedAsNeedingMoreAccessNotAsTampering(t *testing.T) {
	objs := goodObjects(true)
	objs[2].obj.Rules = []kube.PolicyRule{
		{APIGroups: []string{""}, Resources: []string{"nodes", "pods"}, Verbs: []string{"get", "list"}},
		{APIGroups: []string{"apps"}, Resources: []string{"deployments"}, Verbs: []string{"get", "list"}},
	}
	r := inspect(t, cluster(objs...))

	if !r.NeedsMoreAccess() {
		t.Fatal("a role that is only missing the newly required reads should be reported as needing more access")
	}
	var role ObjectState
	for _, o := range r.Objects {
		if o.Kind == KindClusterRole {
			role = o
		}
	}
	if !strings.Contains(role.Detail, "services") || !strings.Contains(role.Detail, "replicasets") {
		t.Errorf("detail should name the added access, got %q", role.Detail)
	}
	for _, c := range Plan(r) {
		if strings.Contains(c.Detail, "reset") || !strings.Contains(c.Detail, "added") {
			t.Errorf("an extension of the role must not be described as a reset: %q", c.Detail)
		}
	}
}

func TestAWidenedRoleIsStillDescribedAsAResetAndNeverAsNeedingMoreAccess(t *testing.T) {
	objs := goodObjects(true)
	objs[2].obj.Rules = append(objs[2].obj.Rules, kube.PolicyRule{APIGroups: []string{""}, Resources: []string{"secrets"}, Verbs: []string{"get"}})
	r := inspect(t, cluster(objs...))

	if r.NeedsMoreAccess() {
		t.Fatal("a role that grants more than approved is tampering, not an upgrade")
	}
	for _, c := range Plan(r) {
		if !strings.Contains(c.Detail, "reset") {
			t.Errorf("a widened role must be described as reset: %q", c.Detail)
		}
	}
}

// ---------------------------------------------------------------- the code and the reviewed manifest agree

func TestDesiredObjectsMatchTheReviewedManifest(t *testing.T) {
	f, err := os.Open("../../../../deploy/rbac/resilience-readonly.yaml")
	if err != nil {
		t.Skipf("manifest not available from here: %v", err)
	}
	defer f.Close()
	dec := yaml.NewDecoder(f)
	seen := map[string]bool{}
	for {
		var raw map[string]any
		if err := dec.Decode(&raw); err != nil {
			if !errors.Is(err, io.EOF) {
				t.Fatalf("the manifest is not valid YAML: %v", err)
			}
			break
		}
		if len(raw) == 0 {
			continue
		}
		kind, _ := raw["kind"].(string)
		meta, _ := raw["metadata"].(map[string]any)
		name, _ := meta["name"].(string)
		seen[kind] = true
		switch kind {
		case "Namespace":
			if name != Namespace {
				t.Errorf("namespace %q != %q", name, Namespace)
			}
		case "ServiceAccount":
			if name != Name || meta["namespace"] != Namespace {
				t.Errorf("service account %v/%v", meta["namespace"], name)
			}
		case "ClusterRole":
			var cr struct {
				Rules []kube.PolicyRule `json:"rules"`
			}
			if err := convert(raw, &cr); err != nil {
				t.Fatal(err)
			}
			if name != Name || !equalRules(cr.Rules, desiredRules()) {
				t.Errorf("ClusterRole in code differs from the manifest:\n code     %+v\n manifest %+v", desiredRules(), cr.Rules)
			}
		case "ClusterRoleBinding":
			var b struct {
				RoleRef  kube.RoleRef   `json:"roleRef"`
				Subjects []kube.Subject `json:"subjects"`
			}
			if err := convert(raw, &b); err != nil {
				t.Fatal(err)
			}
			if name != Name || b.RoleRef != desiredRoleRef() || len(b.Subjects) != 1 || b.Subjects[0] != desiredSubject() {
				t.Errorf("ClusterRoleBinding in code differs from the manifest: %+v", b)
			}
		default:
			t.Errorf("manifest contains an object this helper does not manage: %s", kind)
		}
	}
	for _, want := range []string{"Namespace", "ServiceAccount", "ClusterRole", "ClusterRoleBinding"} {
		if !seen[want] {
			t.Errorf("manifest lacks %s", want)
		}
	}
}

func equalRules(a, b []kube.PolicyRule) bool {
	na, nb := normalizeRules(a), normalizeRules(b)
	if len(na) != len(nb) {
		return false
	}
	for i := range na {
		if strings.Join(na[i].Verbs, ",") != strings.Join(nb[i].Verbs, ",") ||
			strings.Join(na[i].Resources, ",") != strings.Join(nb[i].Resources, ",") ||
			strings.Join(na[i].APIGroups, ",") != strings.Join(nb[i].APIGroups, ",") {
			return false
		}
	}
	return true
}

// convert reads a decoded YAML document into typed fields through JSON, which is how the API server's
// own types are described.
func convert(raw map[string]any, out any) error {
	encoded, err := json.Marshal(raw)
	if err != nil {
		return err
	}
	return json.Unmarshal(encoded, out)
}
