package rbac

import (
	"context"
	"os"
	"strings"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	k8syaml "k8s.io/apimachinery/pkg/util/yaml"
	"k8s.io/client-go/kubernetes/fake"
	k8stesting "k8s.io/client-go/testing"
)

var ctx = context.Background()

// ---------------------------------------------------------------- builders for cluster state

func plain(m metav1.ObjectMeta) metav1.ObjectMeta { m.Labels = nil; return m }

func goodObjects(labelled bool) []runtime.Object {
	ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: Namespace, Labels: labels()}}
	sa := &corev1.ServiceAccount{ObjectMeta: metav1.ObjectMeta{Name: Name, Namespace: Namespace, Labels: labels()}}
	cr := &rbacv1.ClusterRole{ObjectMeta: metav1.ObjectMeta{Name: Name, Labels: labels()}, Rules: desiredRules()}
	crb := &rbacv1.ClusterRoleBinding{
		ObjectMeta: metav1.ObjectMeta{Name: Name, Labels: labels()},
		RoleRef:    desiredRoleRef(),
		Subjects:   []rbacv1.Subject{desiredSubject()},
	}
	if !labelled {
		ns.ObjectMeta, sa.ObjectMeta, cr.ObjectMeta, crb.ObjectMeta = plain(ns.ObjectMeta), plain(sa.ObjectMeta), plain(cr.ObjectMeta), plain(crb.ObjectMeta)
	}
	return []runtime.Object{ns, sa, cr, crb}
}

// writes returns every write the client was asked to make. Inspect and renewal must make none.
func writes(cs *fake.Clientset) []string {
	var out []string
	for _, a := range cs.Actions() {
		switch a.GetVerb() {
		case "create", "update", "patch", "delete", "delete-collection":
			out = append(out, a.GetVerb()+" "+a.GetResource().Resource+"/"+a.GetSubresource())
		}
	}
	return out
}

func states(r Report) map[Kind]State {
	m := map[Kind]State{}
	for _, o := range r.Objects {
		m[o.Kind] = o.State
	}
	return m
}

func inspect(t *testing.T, cs *fake.Clientset) Report {
	t.Helper()
	r, err := Inspect(ctx, cs)
	if err != nil {
		t.Fatalf("Inspect: %v", err)
	}
	return r
}

// ---------------------------------------------------------------- classification

func TestEmptyClusterIsAbsentAndPlansFourCreates(t *testing.T) {
	cs := fake.NewClientset()
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
	if w := writes(cs); len(w) != 0 {
		t.Fatalf("Inspect must not write, wrote %v", w)
	}
}

func TestExactlyApprovedAccessIsUsableAndNeedsNoChange(t *testing.T) {
	cs := fake.NewClientset(goodObjects(true)...)
	r := inspect(t, cs)

	if r.Overall() != OverallApproved || !r.Usable() || len(Plan(r)) != 0 {
		t.Fatalf("overall = %s, plan = %v", r.Overall(), Plan(r))
	}
}

func TestAccessMadeByTheOldScriptHasNoLabelButIsStillSafeToRenew(t *testing.T) {
	cs := fake.NewClientset(goodObjects(false)...)
	r := inspect(t, cs)

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
	cs := fake.NewClientset(objs[0], objs[1]) // namespace and service account only
	r := inspect(t, cs)

	if r.Overall() != OverallPartial || r.Usable() {
		t.Fatalf("overall = %s usable = %v", r.Overall(), r.Usable())
	}
}

func TestRuleOrderDoesNotMatterButContentDoes(t *testing.T) {
	objs := goodObjects(true)
	cr := objs[2].(*rbacv1.ClusterRole)
	cr.Rules = []rbacv1.PolicyRule{ // same rules, reversed order everywhere
		{APIGroups: []string{"apps"}, Resources: []string{"deployments"}, Verbs: []string{"list", "get"}},
		{APIGroups: []string{""}, Resources: []string{"pods", "nodes"}, Verbs: []string{"list", "get"}},
	}
	if r := inspect(t, fake.NewClientset(objs...)); r.Overall() != OverallApproved {
		t.Fatalf("reordered rules should still match, got %s", r.Overall())
	}
}

func TestWidenedClusterRoleIsDriftedAndNotUsable(t *testing.T) {
	cases := map[string]func(cr *rbacv1.ClusterRole){
		"reads secrets": func(cr *rbacv1.ClusterRole) {
			cr.Rules = append(cr.Rules, rbacv1.PolicyRule{APIGroups: []string{""}, Resources: []string{"secrets"}, Verbs: []string{"get"}})
		},
		"adds a write verb": func(cr *rbacv1.ClusterRole) { cr.Rules[0].Verbs = append(cr.Rules[0].Verbs, "delete") },
		"wildcard": func(cr *rbacv1.ClusterRole) {
			cr.Rules = []rbacv1.PolicyRule{{APIGroups: []string{"*"}, Resources: []string{"*"}, Verbs: []string{"*"}}}
		},
		"drops a rule": func(cr *rbacv1.ClusterRole) { cr.Rules = cr.Rules[:1] },
		"aggregation": func(cr *rbacv1.ClusterRole) {
			cr.AggregationRule = &rbacv1.AggregationRule{ClusterRoleSelectors: []metav1.LabelSelector{{MatchLabels: map[string]string{"x": "y"}}}}
		},
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			objs := goodObjects(true)
			mutate(objs[2].(*rbacv1.ClusterRole))
			r := inspect(t, fake.NewClientset(objs...))
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
		objs[3].(*rbacv1.ClusterRoleBinding).RoleRef = rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "ClusterRole", Name: "cluster-admin"}
		if r := inspect(t, fake.NewClientset(objs...)); r.Overall() != OverallDrifted || r.Usable() {
			t.Fatalf("overall = %s", r.Overall())
		}
	})
	t.Run("extra subject", func(t *testing.T) {
		objs := goodObjects(true)
		b := objs[3].(*rbacv1.ClusterRoleBinding)
		b.Subjects = append(b.Subjects, rbacv1.Subject{Kind: rbacv1.UserKind, Name: "mallory", APIGroup: rbacv1.GroupName})
		if r := inspect(t, fake.NewClientset(objs...)); r.Overall() != OverallDrifted || r.Usable() {
			t.Fatalf("overall = %s", r.Overall())
		}
	})
}

func TestSomeoneElsesLabelIsDrift(t *testing.T) {
	objs := goodObjects(true)
	objs[2].(*rbacv1.ClusterRole).Labels = map[string]string{ManagedByKey: "helm"}
	if r := inspect(t, fake.NewClientset(objs...)); r.Overall() != OverallDrifted {
		t.Fatalf("overall = %s", r.Overall())
	}
}

func TestAnotherBindingThatGrantsTheServiceAccountMoreMakesItOverGranted(t *testing.T) {
	adminCRB := &rbacv1.ClusterRoleBinding{
		ObjectMeta: metav1.ObjectMeta{Name: "grant-admin"},
		RoleRef:    rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "ClusterRole", Name: "cluster-admin"},
		Subjects:   []rbacv1.Subject{desiredSubject()},
	}
	nsRB := &rbacv1.RoleBinding{
		ObjectMeta: metav1.ObjectMeta{Name: "edit-default", Namespace: "default"},
		RoleRef:    rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "ClusterRole", Name: "edit"},
		Subjects:   []rbacv1.Subject{desiredSubject()},
	}
	for name, extra := range map[string]runtime.Object{"cluster binding": adminCRB, "namespaced binding": nsRB} {
		t.Run(name, func(t *testing.T) {
			cs := fake.NewClientset(append(goodObjects(true), extra)...)
			r := inspect(t, cs)
			if r.Overall() != OverallOverGranted || r.Usable() || len(r.ExtraGrants) != 1 {
				t.Fatalf("overall = %s grants = %v", r.Overall(), r.ExtraGrants)
			}
			cs.ClearActions()
			if err := Apply(ctx, cs, r); err != ErrOverGranted {
				t.Fatalf("Apply = %v, want ErrOverGranted", err)
			}
			if w := writes(cs); len(w) != 0 {
				t.Fatalf("a refused Apply must not write, wrote %v", w)
			}
		})
	}
}

func TestOurOwnBindingIsNotCountedAsAnExtraGrant(t *testing.T) {
	if r := inspect(t, fake.NewClientset(goodObjects(true)...)); len(r.ExtraGrants) != 0 {
		t.Fatalf("grants = %v", r.ExtraGrants)
	}
}

// ---------------------------------------------------------------- apply

func TestApplyCreatesEverythingLabelledAndTheResultInspectsAsApproved(t *testing.T) {
	cs := fake.NewClientset()
	if err := Apply(ctx, cs, inspect(t, cs)); err != nil {
		t.Fatal(err)
	}
	cs.ClearActions()
	r := inspect(t, cs)
	if r.Overall() != OverallApproved || len(Plan(r)) != 0 {
		t.Fatalf("overall = %s plan = %v", r.Overall(), Plan(r))
	}
	cr, _ := cs.RbacV1().ClusterRoles().Get(ctx, Name, metav1.GetOptions{})
	if cr.Labels[ManagedByKey] != ManagedByValue {
		t.Fatal("created objects must carry the ownership label")
	}
}

func TestApplyRepairsDriftAndTakesNothingOutsideItsOwnFourObjects(t *testing.T) {
	objs := goodObjects(true)
	cr := objs[2].(*rbacv1.ClusterRole)
	cr.Rules = append(cr.Rules, rbacv1.PolicyRule{APIGroups: []string{""}, Resources: []string{"secrets"}, Verbs: []string{"get"}})
	objs[3].(*rbacv1.ClusterRoleBinding).RoleRef = rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "ClusterRole", Name: "cluster-admin"}
	other := &rbacv1.ClusterRole{ObjectMeta: metav1.ObjectMeta{Name: "someone-elses"}}
	cs := fake.NewClientset(append(objs, other)...)

	if err := Apply(ctx, cs, inspect(t, cs)); err != nil {
		t.Fatal(err)
	}
	if r := inspect(t, cs); r.Overall() != OverallApproved {
		t.Fatalf("after repair overall = %s", r.Overall())
	}
	if _, err := cs.RbacV1().ClusterRoles().Get(ctx, "someone-elses", metav1.GetOptions{}); err != nil {
		t.Fatalf("unrelated object was touched: %v", err)
	}
}

func TestApplyOnlyLabelsLegacyObjectsAndLeavesContentAlone(t *testing.T) {
	cs := fake.NewClientset(goodObjects(false)...)
	cs.ClearActions()
	if err := Apply(ctx, cs, inspect(t, cs)); err != nil {
		t.Fatal(err)
	}
	for _, w := range writes(cs) {
		if !strings.HasPrefix(w, "update ") {
			t.Fatalf("legacy labelling must only update, did %q", w)
		}
	}
	if r := inspect(t, cs); r.Overall() != OverallApproved {
		t.Fatalf("overall = %s", r.Overall())
	}
}

func TestApplyOnAnAlreadyApprovedClusterWritesNothing(t *testing.T) {
	cs := fake.NewClientset(goodObjects(true)...)
	r := inspect(t, cs)
	cs.ClearActions()
	if err := Apply(ctx, cs, r); err != nil {
		t.Fatal(err)
	}
	if w := writes(cs); len(w) != 0 {
		t.Fatalf("wrote %v", w)
	}
}

// ---------------------------------------------------------------- RBAC denial of the USER's kubeconfig

func forbid(cs *fake.Clientset, verb, resource string) {
	cs.PrependReactor(verb, resource, func(a k8stesting.Action) (bool, runtime.Object, error) {
		return true, nil, apierrors.NewForbidden(schema.GroupResource{Resource: resource}, "x", nil)
	})
}

func TestApplyReportsForbiddenWhenTheUsersKubeconfigCannotManageRBAC(t *testing.T) {
	cs := fake.NewClientset()
	forbid(cs, "create", "clusterroles")
	err := Apply(ctx, cs, inspect(t, cs))
	if err == nil || !strings.Contains(err.Error(), ErrForbidden.Error()) {
		t.Fatalf("err = %v", err)
	}
}

func TestInspectReportsForbiddenWhenTheUserCannotReadRBAC(t *testing.T) {
	cs := fake.NewClientset()
	forbid(cs, "get", "clusterroles")
	if _, err := Inspect(ctx, cs); err == nil || !strings.Contains(err.Error(), ErrForbidden.Error()) {
		t.Fatalf("err = %v", err)
	}
}

// ---------------------------------------------------------------- revoke (fails closed)

func planFor(t *testing.T, cs *fake.Clientset) RevokePlan {
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
	cs := fake.NewClientset(goodObjects(true)...)
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
	in := func(name string) metav1.ObjectMeta { return metav1.ObjectMeta{Name: name, Namespace: Namespace} }
	things := map[string]runtime.Object{
		"another service account": &corev1.ServiceAccount{ObjectMeta: in("neighbour")},
		"a pod":                   &corev1.Pod{ObjectMeta: in("p")},
		"a service":               &corev1.Service{ObjectMeta: in("s")},
		"a secret":                &corev1.Secret{ObjectMeta: in("db-password")},
		"a config map":            &corev1.ConfigMap{ObjectMeta: in("settings")},
		"a volume claim":          &corev1.PersistentVolumeClaim{ObjectMeta: in("data")},
		"a deployment":            &appsv1.Deployment{ObjectMeta: in("d")},
		"a stateful set":          &appsv1.StatefulSet{ObjectMeta: in("db")},
		"a daemon set":            &appsv1.DaemonSet{ObjectMeta: in("agent")},
		"a job":                   &batchv1.Job{ObjectMeta: in("j")},
		"a cron job":              &batchv1.CronJob{ObjectMeta: in("c")},
		"an ingress":              &networkingv1.Ingress{ObjectMeta: in("i")},
		"a role":                  &rbacv1.Role{ObjectMeta: in("r")},
		"a role binding":          &rbacv1.RoleBinding{ObjectMeta: in("rb")},
	}
	for name, obj := range things {
		t.Run(name, func(t *testing.T) {
			cs := fake.NewClientset(append(goodObjects(true), obj)...)
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
			if _, err := cs.CoreV1().Namespaces().Get(ctx, Namespace, metav1.GetOptions{}); err != nil {
				t.Fatalf("namespace was deleted although it holds %s", name)
			}
			if _, err := cs.RbacV1().ClusterRoles().Get(ctx, Name, metav1.GetOptions{}); err == nil {
				t.Fatal("the access objects should still be removed")
			}
		})
	}
}

func TestTheNamespaceThatKubernetesFillsInItselfDoesNotCountAsContents(t *testing.T) {
	rootCA := &corev1.ConfigMap{ObjectMeta: metav1.ObjectMeta{Name: "kube-root-ca.crt", Namespace: Namespace}}
	cs := fake.NewClientset(append(goodObjects(true), rootCA)...)
	if got := strings.Join(deleted(planFor(t, cs)), ","); !strings.Contains(got, "Namespace") {
		t.Fatalf("kube-root-ca.crt exists in every namespace and must not block deletion, plan = %s", got)
	}
}

func TestANamespaceMadeByTheOldScriptHasNoLabelSoItIsNeverDeleted(t *testing.T) {
	cs := fake.NewClientset(goodObjects(false)...) // as the old setup script left it
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
	if _, err := cs.CoreV1().Namespaces().Get(ctx, Namespace, metav1.GetOptions{}); err != nil {
		t.Fatal("an unlabelled namespace must survive a revoke")
	}
}

func TestIfTheContentsCannotBeListedTheNamespaceIsKeptNotDeleted(t *testing.T) {
	for _, resource := range []string{"pods", "secrets", "configmaps", "deployments", "persistentvolumeclaims", "serviceaccounts"} {
		t.Run(resource, func(t *testing.T) {
			cs := fake.NewClientset(goodObjects(true)...)
			forbid(cs, "list", resource)
			plan := PlanRevoke(ctx, cs, mustReport(t, cs))
			if strings.Contains(strings.Join(deleted(plan), ","), "Namespace") {
				t.Fatalf("doubt must mean keep: could not list %s but the namespace is in the plan", resource)
			}
			if len(plan.Keep) != 1 || !strings.Contains(plan.Keep[0], "could not verify") {
				t.Fatalf("keep = %v", plan.Keep)
			}
		})
	}
}

// mustReport inspects the cluster. The forbidden-list reactors in these tests only cover resources
// Inspect never lists (it lists bindings only), so a report can still be produced.
func mustReport(t *testing.T, cs *fake.Clientset) Report {
	t.Helper()
	r, err := Inspect(ctx, cs)
	if err != nil {
		t.Fatalf("Inspect: %v", err)
	}
	return r
}

func TestObjectsAnotherToolHasLabelledAsItsOwnAreNeverDeleted(t *testing.T) {
	objs := goodObjects(true)
	objs[2].(*rbacv1.ClusterRole).Labels = map[string]string{ManagedByKey: "helm"}
	objs[0].(*corev1.Namespace).Labels = map[string]string{ManagedByKey: "argocd"}
	cs := fake.NewClientset(objs...)
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
	if _, err := cs.RbacV1().ClusterRoles().Get(ctx, Name, metav1.GetOptions{}); err != nil {
		t.Fatal("helm's ClusterRole was deleted")
	}
	if _, err := cs.CoreV1().Namespaces().Get(ctx, Namespace, metav1.GetOptions{}); err != nil {
		t.Fatal("argocd's Namespace was deleted")
	}
}

func TestRevokeExecutesExactlyThePlanAndNothingMore(t *testing.T) {
	cs := fake.NewClientset(goodObjects(true)...)
	only := RevokePlan{Delete: []Change{{Action: "delete", Kind: KindClusterRoleBinding, Name: Name}}}
	if err := Revoke(ctx, cs, only); err != nil {
		t.Fatal(err)
	}
	if _, err := cs.RbacV1().ClusterRoles().Get(ctx, Name, metav1.GetOptions{}); err != nil {
		t.Fatal("the ClusterRole was not in the plan and must remain")
	}
	if _, err := cs.CoreV1().Namespaces().Get(ctx, Namespace, metav1.GetOptions{}); err != nil {
		t.Fatal("the Namespace was not in the plan and must remain")
	}
	if err := Revoke(ctx, cs, RevokePlan{}); err != nil {
		t.Fatalf("an empty plan must do nothing: %v", err)
	}
}

func TestAnEmptyClusterHasNothingToRevoke(t *testing.T) {
	plan := planFor(t, fake.NewClientset())
	if len(plan.Delete) != 0 || len(plan.Keep) != 0 {
		t.Fatalf("plan = %+v", plan)
	}
}

func TestRevokeReportsForbiddenWhenTheUsersKubeconfigCannotDelete(t *testing.T) {
	cs := fake.NewClientset(goodObjects(true)...)
	plan := planFor(t, cs)
	forbid(cs, "delete", "clusterrolebindings")
	if err := Revoke(ctx, cs, plan); err == nil || !strings.Contains(err.Error(), ErrForbidden.Error()) {
		t.Fatalf("err = %v", err)
	}
}

// ---------------------------------------------------------------- the code and the reviewed manifest agree

func TestDesiredObjectsMatchTheReviewedManifest(t *testing.T) {
	f, err := os.Open("../../../../deploy/rbac/resilience-readonly.yaml")
	if err != nil {
		t.Skipf("manifest not available from here: %v", err)
	}
	defer f.Close()
	dec := k8syaml.NewYAMLOrJSONDecoder(f, 4096)
	seen := map[string]bool{}
	for {
		var raw map[string]any
		if err := dec.Decode(&raw); err != nil {
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
			var cr rbacv1.ClusterRole
			if err := runtimeConvert(raw, &cr); err != nil {
				t.Fatal(err)
			}
			if name != Name || !equalRules(cr.Rules, desiredRules()) {
				t.Errorf("ClusterRole in code differs from the manifest:\n code     %+v\n manifest %+v", desiredRules(), cr.Rules)
			}
		case "ClusterRoleBinding":
			var b rbacv1.ClusterRoleBinding
			if err := runtimeConvert(raw, &b); err != nil {
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

func equalRules(a, b []rbacv1.PolicyRule) bool {
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

func runtimeConvert(raw map[string]any, out any) error {
	return runtime.DefaultUnstructuredConverter.FromUnstructured(raw, out)
}
