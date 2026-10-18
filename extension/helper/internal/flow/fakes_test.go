package flow

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	authv1 "k8s.io/api/authentication/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/version"
	fakediscovery "k8s.io/client-go/discovery/fake"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/kubernetes/fake"
	k8stesting "k8s.io/client-go/testing"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/clusters"
	"resilience-engine/helper/internal/kubeconfig"
	"resilience-engine/helper/internal/rbac"
	"resilience-engine/helper/internal/transport"
)

const (
	tokenValue = "eyJ-TOKEN-THAT-MUST-ONLY-REACH-THE-BACKEND"
	ctxName    = "kind-resilience-research"
)

var (
	bg  = context.Background()
	t0  = time.Date(2026, 10, 19, 12, 0, 0, 0, time.UTC)
	pem = []byte("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
)

// ---------------------------------------------------------------- a fake cluster

func newCluster(grant time.Duration) *fake.Clientset {
	cs := fake.NewClientset()
	cs.Discovery().(*fakediscovery.FakeDiscovery).FakedServerVersion = &version.Info{GitVersion: "v1.37.0"}
	cs.PrependReactor("create", "serviceaccounts", func(a k8stesting.Action) (bool, runtime.Object, error) {
		if a.GetSubresource() != "token" {
			return false, nil, nil
		}
		return true, &authv1.TokenRequest{Status: authv1.TokenRequestStatus{
			Token:               tokenValue,
			ExpirationTimestamp: metav1.NewTime(t0.Add(grant)),
		}}, nil
	})
	return cs
}

// approvedCluster is a cluster where the extension's access already exists exactly as approved.
func approvedCluster(t *testing.T) *fake.Clientset {
	t.Helper()
	cs := newCluster(8 * time.Hour)
	r, err := rbac.Inspect(bg, cs)
	if err != nil {
		t.Fatal(err)
	}
	if err := rbac.Apply(bg, cs, r); err != nil {
		t.Fatal(err)
	}
	cs.ClearActions()
	return cs
}

// stripLabels makes the access look as the old setup script left it: right content, no label.
func stripLabels(t *testing.T, cs *fake.Clientset) {
	t.Helper()
	ns, _ := cs.CoreV1().Namespaces().Get(bg, rbac.Namespace, metav1.GetOptions{})
	ns.Labels = nil
	cs.CoreV1().Namespaces().Update(bg, ns, metav1.UpdateOptions{})
	sa, _ := cs.CoreV1().ServiceAccounts(rbac.Namespace).Get(bg, rbac.Name, metav1.GetOptions{})
	sa.Labels = nil
	cs.CoreV1().ServiceAccounts(rbac.Namespace).Update(bg, sa, metav1.UpdateOptions{})
	cr, _ := cs.RbacV1().ClusterRoles().Get(bg, rbac.Name, metav1.GetOptions{})
	cr.Labels = nil
	cs.RbacV1().ClusterRoles().Update(bg, cr, metav1.UpdateOptions{})
	crb, _ := cs.RbacV1().ClusterRoleBindings().Get(bg, rbac.Name, metav1.GetOptions{})
	crb.Labels = nil
	cs.RbacV1().ClusterRoleBindings().Update(bg, crb, metav1.UpdateOptions{})
	cs.ClearActions()
}

func widenRole(t *testing.T, cs *fake.Clientset) {
	t.Helper()
	cr, _ := cs.RbacV1().ClusterRoles().Get(bg, rbac.Name, metav1.GetOptions{})
	cr.Rules = append(cr.Rules, rbacv1.PolicyRule{APIGroups: []string{""}, Resources: []string{"secrets"}, Verbs: []string{"get", "list"}})
	if _, err := cs.RbacV1().ClusterRoles().Update(bg, cr, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	cs.ClearActions()
}

func addExtraBinding(t *testing.T, cs *fake.Clientset) {
	t.Helper()
	_, err := cs.RbacV1().ClusterRoleBindings().Create(bg, &rbacv1.ClusterRoleBinding{
		ObjectMeta: metav1.ObjectMeta{Name: "grant-admin"},
		RoleRef:    rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "ClusterRole", Name: "cluster-admin"},
		Subjects:   []rbacv1.Subject{{Kind: rbacv1.ServiceAccountKind, Name: rbac.Name, Namespace: rbac.Namespace}},
	}, metav1.CreateOptions{})
	if err != nil {
		t.Fatal(err)
	}
	cs.ClearActions()
}

func forbid(cs *fake.Clientset, verb, resource string) {
	cs.PrependReactor(verb, resource, func(a k8stesting.Action) (bool, runtime.Object, error) {
		return true, nil, apierrors.NewForbidden(schema.GroupResource{Resource: resource}, "x", nil)
	})
}

// clusterWrites lists every write made to the cluster. A token request counts separately.
func clusterWrites(cs *fake.Clientset) (writes []string, tokenRequests int) {
	for _, a := range cs.Actions() {
		switch a.GetVerb() {
		case "create", "update", "patch", "delete", "delete-collection":
			if a.GetResource().Resource == "serviceaccounts" && a.GetSubresource() == "token" {
				tokenRequests++
				continue
			}
			writes = append(writes, a.GetVerb()+" "+a.GetResource().Resource)
		}
	}
	return
}

// ---------------------------------------------------------------- a fake kubeconfig

type fakeLoader struct {
	contexts []clusters.Context
	clients  map[string]*fake.Clientset
	called   map[string]int
}

func newLoader(cs *fake.Clientset) *fakeLoader {
	return &fakeLoader{
		contexts: []clusters.Context{
			{Name: ctxName, Server: "https://127.0.0.1:58227"},
			{Name: "docker-desktop", Server: "https://kubernetes.docker.internal:6443"},
			{Name: "prod-cluster", Server: "https://prod.example.com:6443"},
			{Name: "minikube", Server: "https://127.0.0.1:8443"},
		},
		clients: map[string]*fake.Clientset{ctxName: cs},
		called:  map[string]int{},
	}
}

func (l *fakeLoader) Contexts() ([]clusters.Context, string, error) { return l.contexts, ctxName, nil }

func (l *fakeLoader) Client(name string) (kubernetes.Interface, kubeconfig.Cluster, error) {
	l.called[name]++
	cs, ok := l.clients[name]
	if !ok {
		return nil, kubeconfig.Cluster{}, fmt.Errorf("no client for %s", name)
	}
	server := "https://127.0.0.1:58227"
	for _, c := range l.contexts {
		if c.Name == name {
			server = c.Server
		}
	}
	return cs, kubeconfig.Cluster{Server: server, CAData: pem}, nil
}

// ---------------------------------------------------------------- a fake dialog

type fakePrompter struct {
	decision approve.Decision
	err      error
	requests []approve.Request
}

func (p *fakePrompter) Confirm(_ context.Context, r approve.Request) (approve.Decision, error) {
	p.requests = append(p.requests, r)
	return p.decision, p.err
}

func answer(d approve.Decision) *fakePrompter { return &fakePrompter{decision: d} }

// ---------------------------------------------------------------- a fake backend

type fakeBackend struct {
	name       string
	status     transport.Status
	statusErr  error
	installErr error
	installs   []transport.InstallRequest
	disconnect int
	disconnErr error
	renewAt    string
}

func (b *fakeBackend) Name() string { return b.name }
func (b *fakeBackend) Status(context.Context) (transport.Status, error) {
	return b.status, b.statusErr
}
func (b *fakeBackend) Install(_ context.Context, r transport.InstallRequest) (transport.Status, error) {
	if b.installErr != nil {
		return transport.Status{}, b.installErr
	}
	b.installs = append(b.installs, r)
	return transport.Status{Connected: true, Managed: true, Context: r.Context, State: "ok", RenewAt: b.renewAt}, nil
}
func (b *fakeBackend) Disconnect(context.Context) error { b.disconnect++; return b.disconnErr }

func newBackend() *fakeBackend {
	return &fakeBackend{name: "fake-transport", status: transport.Status{Connected: false}, renewAt: "2026-10-19T16:00:00Z"}
}

// managedBackend is a backend that already holds a managed credential for the test cluster.
func managedBackend() *fakeBackend {
	b := newBackend()
	b.status = transport.Status{Connected: true, Managed: true, Context: ctxName, State: "renew-soon"}
	return b
}

func deps(l *fakeLoader, p *fakePrompter, b *fakeBackend) Deps {
	return Deps{
		Clusters: l,
		Prompter: p,
		Backends: func() []transport.Backend { return []transport.Backend{b} },
		Now:      func() time.Time { return t0 },
	}
}

// ---------------------------------------------------------------- assertions

// noLeak asserts no credential appears in anything a Result can show, and (the positive control)
// that the backend really did receive the token, so "not found" cannot be vacuous.
func noLeak(t *testing.T, results ...Result) {
	t.Helper()
	for _, r := range results {
		out, err := json.Marshal(r)
		if err != nil {
			t.Fatal(err)
		}
		for _, bad := range []string{tokenValue, "eyJ", "BEGIN CERTIFICATE", "token:"} {
			if strings.Contains(string(out), bad) {
				t.Fatalf("a Result leaked %q: %s", bad, out)
			}
		}
	}
}

func receivedToken(b *fakeBackend) bool {
	for _, r := range b.installs {
		if strings.Contains(string(r.Kubeconfig.Reveal()), tokenValue) {
			return true
		}
	}
	return false
}

var _ = corev1.Namespace{}

type (
	k8stestingAction = k8stesting.Action
	runtimeObject    = runtime.Object
)

func notFound(resource, name string) error {
	return apierrors.NewNotFound(schema.GroupResource{Resource: resource}, name)
}

func forbiddenErr(resource string) error {
	return apierrors.NewForbidden(schema.GroupResource{Resource: resource}, "x", nil)
}

type errString string

func (e errString) Error() string { return string(e) }

// removeAccess deletes the extension's access the way a cluster administrator would.
func removeAccess(t *testing.T, cs *fake.Clientset) {
	t.Helper()
	r, err := rbac.Inspect(bg, cs)
	if err != nil {
		t.Fatal(err)
	}
	if err := rbac.Revoke(bg, cs, rbac.PlanRevoke(bg, cs, r)); err != nil {
		t.Fatal(err)
	}
	cs.ClearActions()
}
