package flow

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/agent"
	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/clusters"
	"resilience-engine/helper/internal/kube"
	"resilience-engine/helper/internal/kube/kubetest"
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

func newCluster(grant time.Duration) *kubetest.Fake {
	cs := kubetest.New()
	cs.GrantTokens(tokenValue, t0, grant)
	cs.Put(kube.Nodes, kube.Object{Name: "kind-control-plane", ProviderID: "kind://docker/resilience-research/kind-control-plane"})
	return cs
}

// approvedCluster is a cluster where the extension's access already exists exactly as approved.
func approvedCluster(t *testing.T) *kubetest.Fake {
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

// edit changes one stored object the way another administrator would, then forgets the action.
func edit(t *testing.T, cs *kubetest.Fake, r kube.Resource, namespace, name string, change func(*kube.Object)) {
	t.Helper()
	o, ok := cs.Lookup(r, namespace, name)
	if !ok {
		t.Fatalf("%s %s/%s is not in the cluster", r.Plural, namespace, name)
	}
	change(&o)
	cs.Put(r, o)
}

// stripLabels makes the access look as the old setup script left it: right content, no label.
func stripLabels(t *testing.T, cs *kubetest.Fake) {
	t.Helper()
	clear := func(o *kube.Object) { o.Labels = nil }
	edit(t, cs, kube.Namespaces, "", rbac.Namespace, clear)
	edit(t, cs, kube.ServiceAccounts, rbac.Namespace, rbac.Name, clear)
	edit(t, cs, kube.ClusterRoles, "", rbac.Name, clear)
	edit(t, cs, kube.ClusterRoleBindings, "", rbac.Name, clear)
	cs.ClearActions()
}

func widenRole(t *testing.T, cs *kubetest.Fake) {
	t.Helper()
	edit(t, cs, kube.ClusterRoles, "", rbac.Name, func(cr *kube.Object) {
		cr.Rules = append(cr.Rules, kube.PolicyRule{APIGroups: []string{""}, Resources: []string{"secrets"}, Verbs: []string{"get", "list"}})
	})
	cs.ClearActions()
}

func addExtraBinding(t *testing.T, cs *kubetest.Fake) {
	t.Helper()
	cs.Put(kube.ClusterRoleBindings, kube.Object{
		Name:     "grant-admin",
		RoleRef:  kube.RoleRef{APIGroup: kube.RBACGroup, Kind: "ClusterRole", Name: "cluster-admin"},
		Subjects: []kube.Subject{{Kind: kube.ServiceAccountKind, Name: rbac.Name, Namespace: rbac.Namespace}},
	})
	cs.ClearActions()
}

func forbid(cs *kubetest.Fake, verb, resource string) {
	cs.Fail(verb, resource, forbiddenErr(resource))
}

// clusterWrites lists every write made to the cluster. A token request counts separately.
func clusterWrites(cs *kubetest.Fake) (writes []string, tokenRequests int) { return cs.Writes() }

// ---------------------------------------------------------------- a fake kubeconfig

type fakeLoader struct {
	contexts []clusters.Context
	clients  map[string]*kubetest.Fake
	called   map[string]int
}

func newLoader(cs *kubetest.Fake) *fakeLoader {
	return &fakeLoader{
		contexts: []clusters.Context{
			{Name: ctxName, Server: "https://127.0.0.1:58227"},
			{Name: "docker-desktop", Server: "https://kubernetes.docker.internal:6443"},
			{Name: "prod-cluster", Server: "https://prod.example.com:6443"},
			{Name: "minikube", Server: "https://127.0.0.1:8443"},
		},
		clients: map[string]*kubetest.Fake{ctxName: cs},
		called:  map[string]int{},
	}
}

func (l *fakeLoader) Contexts() ([]clusters.Context, string, error) { return l.contexts, ctxName, nil }

func (l *fakeLoader) Client(name string) (kube.Client, kubeconfig.Cluster, error) {
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

func notFound(resource, name string) error {
	return kube.NewAPIError(404, "NotFound", resource+` "`+name+`" not found`)
}

func forbiddenErr(resource string) error {
	return kube.NewAPIError(403, "Forbidden", resource+" is forbidden")
}

type errString string

func (e errString) Error() string { return string(e) }

// removeAccess deletes the extension's access the way a cluster administrator would.
func removeAccess(t *testing.T, cs *kubetest.Fake) {
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

// ---------------------------------------------------------------- a fake background job

type fakeAgent struct {
	supported  bool
	state      agent.State
	installErr error
	installs   int
	uninstalls int
}

func (a *fakeAgent) Supported() bool  { return a.supported }
func (a *fakeAgent) Describe() string { return "a test background job" }
func (a *fakeAgent) Install(context.Context) error {
	if a.installErr != nil {
		return a.installErr
	}
	a.installs++
	a.state = agent.On
	return nil
}
func (a *fakeAgent) Uninstall(context.Context) error {
	a.uninstalls++
	a.state = agent.Off
	return nil
}
func (a *fakeAgent) Status(context.Context) (agent.State, error) {
	if !a.supported {
		return agent.Unsupported, nil
	}
	return a.state, nil
}

func macAgent() *fakeAgent { return &fakeAgent{supported: true, state: agent.Off} }

// withAgent adds a background job to test dependencies.
func withAgent(d Deps, a agent.Controller) Deps { d.Agent = a; return d }
