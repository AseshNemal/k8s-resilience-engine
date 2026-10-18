package flow

import (
	"strings"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/rbac"
	"resilience-engine/helper/internal/transport"
)

func TestRevokeAfterApprovalRemovesTheAccessAndDisconnectsTheBackend(t *testing.T) {
	cs := approvedCluster(t)
	p, b := answer(approve.Approved), managedBackend()
	r := Revoke(bg, deps(newLoader(cs), p, b), ctxName)

	if !r.OK || r.State != StateDisconnected || b.disconnect != 1 {
		t.Fatalf("result %+v, disconnects %d", r, b.disconnect)
	}
	if len(p.requests) != 1 || !strings.Contains(p.requests[0].Body, "DELETE") || !strings.Contains(p.requests[0].Body, "stops working") {
		t.Fatalf("the dialog must say exactly what is deleted:\n%v", p.requests)
	}
	for _, want := range []string{"ClusterRoleBinding", "ClusterRole ", "ServiceAccount", "Namespace"} {
		if !strings.Contains(p.requests[0].Body, want) {
			t.Errorf("dialog lacks %q", want)
		}
	}
	if rep, _ := rbac.Inspect(bg, cs); rep.Overall() != rbac.OverallAbsent {
		t.Fatalf("after revoke: %s", rep.Overall())
	}
}

func TestDecliningARevokeDeletesNothingAndLeavesTheBackendConnected(t *testing.T) {
	for _, d := range []approve.Decision{approve.Declined, approve.TimedOut} {
		cs := approvedCluster(t)
		b := managedBackend()
		r := Revoke(bg, deps(newLoader(cs), answer(d), b), ctxName)

		if r.OK {
			t.Fatalf("%s: %+v", d, r)
		}
		if w, tr := clusterWrites(cs); len(w) != 0 || tr != 0 || b.disconnect != 0 {
			t.Fatalf("%s must change nothing: wrote %v, disconnects %d", d, w, b.disconnect)
		}
	}
}

func TestRevokeWithNoDialogAvailableChangesNothing(t *testing.T) {
	cs := approvedCluster(t)
	b := managedBackend()
	r := Revoke(bg, deps(newLoader(cs), &fakePrompter{err: approve.ErrNoApprovalChannel}, b), ctxName)
	if r.State != StateApprovalUnavailable || b.disconnect != 0 {
		t.Fatalf("result %+v", r)
	}
	if w, _ := clusterWrites(cs); len(w) != 0 {
		t.Fatalf("wrote %v", w)
	}
}

func TestRevokeWhenTheClusterSideIsAlreadyGoneStillDisconnectsWithoutAskingAgain(t *testing.T) {
	cs := newCluster(time.Hour) // nothing to delete
	p, b := answer(approve.Declined), managedBackend()
	r := Revoke(bg, deps(newLoader(cs), p, b), ctxName)

	if !r.OK || b.disconnect != 1 || len(p.requests) != 0 {
		t.Fatalf("result %+v, disconnects %d, dialogs %d", r, b.disconnect, len(p.requests))
	}
}

func TestRevokeKeepsANamespaceThatHoldsOtherThings(t *testing.T) {
	cs := approvedCluster(t)
	cs.CoreV1().ServiceAccounts(rbac.Namespace).Create(bg, &corev1.ServiceAccount{ObjectMeta: metav1.ObjectMeta{Name: "neighbour", Namespace: rbac.Namespace}}, metav1.CreateOptions{})
	Revoke(bg, deps(newLoader(cs), answer(approve.Approved), managedBackend()), ctxName)
	if _, err := cs.CoreV1().Namespaces().Get(bg, rbac.Namespace, metav1.GetOptions{}); err != nil {
		t.Fatalf("the namespace must be kept: %v", err)
	}
}

func TestRevokeSaysSoWhenTheBackendCannotBeReachedAfterwards(t *testing.T) {
	cs := approvedCluster(t)
	b := managedBackend()
	b.statusErr = transport.ErrUnavailable
	r := Revoke(bg, deps(newLoader(cs), answer(approve.Approved), b), ctxName)
	if !r.OK || !strings.Contains(r.Message, "backend is not reachable") {
		t.Fatalf("result %+v", r)
	}
}

func TestRevokeRefusesUnsupportedContexts(t *testing.T) {
	cs := approvedCluster(t)
	for _, name := range []string{"docker-desktop", "prod-cluster", "nope"} {
		l := newLoader(cs)
		r := Revoke(bg, deps(l, answer(approve.Approved), managedBackend()), name)
		if r.State != StateUnsupportedContext || l.called[name] != 0 {
			t.Errorf("%s: %+v", name, r)
		}
	}
}

func TestStatusReportsTheBackendWithoutAnyCredential(t *testing.T) {
	b := managedBackend()
	r := Status(bg, deps(newLoader(newCluster(time.Hour)), answer(approve.Declined), b))
	if !r.OK || r.State != StateConnected || r.Backend == nil || r.Backend.Context != ctxName {
		t.Fatalf("result %+v", r)
	}
	noLeak(t, r)

	b2 := newBackend()
	if r := Status(bg, deps(newLoader(newCluster(time.Hour)), answer(approve.Declined), b2)); r.State != StateNotConnected {
		t.Fatalf("result %+v", r)
	}
	b3 := newBackend()
	b3.statusErr = transport.ErrUnavailable
	if r := Status(bg, deps(newLoader(newCluster(time.Hour)), answer(approve.Declined), b3)); r.OK || r.State != StateBackendUnavailable {
		t.Fatalf("result %+v", r)
	}
}

func TestRevokeOnAnOldScriptClusterKeepsTheUnlabelledNamespaceAndSaysSoBeforeAndAfter(t *testing.T) {
	cs := approvedCluster(t)
	stripLabels(t, cs) // exactly how the old setup script left this machine's cluster
	p, b := answer(approve.Approved), managedBackend()
	r := Revoke(bg, deps(newLoader(cs), p, b), ctxName)

	if !r.OK || b.disconnect != 1 {
		t.Fatalf("result %+v", r)
	}
	body := p.requests[0].Body
	if !strings.Contains(body, "This will NOT be deleted") || !strings.Contains(body, "not created by this extension") {
		t.Fatalf("the dialog must say what is kept:\n%s", body)
	}
	if strings.Contains(body, "Delete Namespace") {
		t.Fatalf("the dialog must not offer to delete an unlabelled namespace:\n%s", body)
	}
	if !strings.Contains(r.Message, "Kept:") || !strings.Contains(r.Message, "Namespace resilience-engine kept") {
		t.Fatalf("the result must repeat what was kept: %q", r.Message)
	}
	if _, err := cs.CoreV1().Namespaces().Get(bg, rbac.Namespace, metav1.GetOptions{}); err != nil {
		t.Fatal("the namespace must survive")
	}
	if rep, _ := rbac.Inspect(bg, cs); rep.Overall() == rbac.OverallApproved || rep.Overall() == rbac.OverallApprovedOld {
		t.Fatalf("the access itself must be gone, found %s", rep.Overall())
	}
}

func TestRevokeNeverDeletesANamespaceHoldingAWorkloadEvenWhenLabelled(t *testing.T) {
	cs := approvedCluster(t)
	cs.CoreV1().Pods(rbac.Namespace).Create(bg, &corev1.Pod{ObjectMeta: metav1.ObjectMeta{Name: "users-app", Namespace: rbac.Namespace}}, metav1.CreateOptions{})
	cs.CoreV1().Secrets(rbac.Namespace).Create(bg, &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "db-password", Namespace: rbac.Namespace}}, metav1.CreateOptions{})
	p := answer(approve.Approved)
	Revoke(bg, deps(newLoader(cs), p, managedBackend()), ctxName)

	if !strings.Contains(p.requests[0].Body, "contains 1 pods") || !strings.Contains(p.requests[0].Body, "contains 1 secrets") {
		t.Fatalf("the dialog must say why the namespace is kept:\n%s", p.requests[0].Body)
	}
	if _, err := cs.CoreV1().Pods(rbac.Namespace).Get(bg, "users-app", metav1.GetOptions{}); err != nil {
		t.Fatal("a user's pod was deleted along with a namespace")
	}
	if _, err := cs.CoreV1().Secrets(rbac.Namespace).Get(bg, "db-password", metav1.GetOptions{}); err != nil {
		t.Fatal("a user's secret was deleted along with a namespace")
	}
}

func TestRevokeKeepsAnythingAnotherToolOwns(t *testing.T) {
	cs := approvedCluster(t)
	cr, _ := cs.RbacV1().ClusterRoles().Get(bg, rbac.Name, metav1.GetOptions{})
	cr.Labels = map[string]string{rbac.ManagedByKey: "helm"}
	cs.RbacV1().ClusterRoles().Update(bg, cr, metav1.UpdateOptions{})
	p := answer(approve.Approved)
	Revoke(bg, deps(newLoader(cs), p, managedBackend()), ctxName)

	if !strings.Contains(p.requests[0].Body, `managed by "helm"`) {
		t.Fatalf("the dialog must name the other owner:\n%s", p.requests[0].Body)
	}
	if _, err := cs.RbacV1().ClusterRoles().Get(bg, rbac.Name, metav1.GetOptions{}); err != nil {
		t.Fatal("helm's ClusterRole was deleted")
	}
}
