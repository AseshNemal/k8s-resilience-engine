package flow

import (
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/clusters"
	"resilience-engine/helper/internal/kube"
	"resilience-engine/helper/internal/kube/kubetest"
	"resilience-engine/helper/internal/kubeconfig"
)

// The candidate gate is the context name (kind-* or docker-desktop), an https API server and a loopback
// address. Whether the cluster really is a local kind cluster is then decided by what it reports, never by
// its name alone: verified TLS, and every node's provider ID.

func TestClassifyAcceptsKindContextsAndDockerDesktopOnLoopbackHttps(t *testing.T) {
	good := []struct{ name, server string }{
		{"kind-a", "https://127.0.0.1:1234"}, {"kind-a", "https://localhost:1"}, {"kind-a", "https://[::1]:6443"},
		{"docker-desktop", "https://127.0.0.1:50345"}, {"docker-desktop", "https://localhost:6443"},
	}
	for _, g := range good {
		if ok, why := Classify(g.name, g.server); !ok {
			t.Errorf("%v refused: %s", g, why)
		}
	}
}

func TestClassifyRefusesEverythingElseWithoutAnyNetworkAccess(t *testing.T) {
	bad := []struct{ name, server, reason string }{
		{"kind-a", "http://127.0.0.1:1234", "https"},
		{"kind-a", "https://10.0.0.1:6443", "loopback"},
		{"kind-a", "https://127.0.0.1.evil.com:6443", "loopback"},
		{"kind-a", "https://127.0.0.1@evil.com:6443", "loopback"},
		{"docker-desktop", "https://kubernetes.docker.internal:6443", "loopback"}, // Docker Desktop's other, kubeadm mode
		{"docker-desktop", "http://127.0.0.1:50345", "https"},
		{"minikube", "https://127.0.0.1:8443", "kind-*"},
		{"prod-cluster", "https://127.0.0.1:6443", "kind-*"}, // a tunnel to a remote cluster, named neither way
		{"docker-desktop-evil", "https://127.0.0.1:6443", "kind-*"},
		{"Docker-Desktop", "https://127.0.0.1:6443", "kind-*"},
		{"kind-a", "", "address"}, {"kind-a", "::::", "address"},
	}
	for _, b := range bad {
		ok, why := Classify(b.name, b.server)
		if ok || !strings.Contains(why, b.reason) {
			t.Errorf("%v: ok=%v reason=%q, want a refusal mentioning %q", b, ok, why, b.reason)
		}
	}
}

// ---------------------------------------------------------------- what the cluster itself reports

func kindNodes(cs *kubetest.Fake, names ...string) {
	for _, n := range names {
		cs.Put(kube.Nodes, kube.Object{Name: n, ProviderID: "kind://docker/desktop/" + n})
	}
}

func verifyWith(cs *kubetest.Fake, ca []byte) verdict {
	return verifyLocalKind(bg, cs, kubeconfig.Cluster{Server: "https://127.0.0.1:50345", CAData: ca})
}

func TestAClusterWhoseEveryNodeReportsAKindProviderIsSupported(t *testing.T) {
	cs := kubetest.New()
	kindNodes(cs, "desktop-control-plane", "desktop-worker", "desktop-worker2")

	v := verifyWith(cs, pem)

	if !v.Supported || !v.Reachable || v.Version != "v1.37.0" || v.Reason != "" || v.Nodes != 3 {
		t.Fatalf("verdict = %+v", v)
	}
	if writes, tokens := cs.Writes(); len(writes) != 0 || tokens != 0 {
		t.Fatalf("verifying must only read: %v %d", writes, tokens)
	}
}

func TestAClusterThatCannotBeVerifiedIsRefusedAndSaysWhy(t *testing.T) {
	cases := map[string]struct {
		build  func(*kubetest.Fake)
		ca     []byte
		reason string
	}{
		"a cloud provider": {func(cs *kubetest.Fake) {
			cs.Put(kube.Nodes, kube.Object{Name: "n", ProviderID: "aws:///us-east-1a/i-0abc"})
		}, pem, "provider"},
		"one node not kind": {func(cs *kubetest.Fake) {
			kindNodes(cs, "a")
			cs.Put(kube.Nodes, kube.Object{Name: "b", ProviderID: "gce://p/z/b"})
		}, pem, "provider"},
		"no provider ID at all":    {func(cs *kubetest.Fake) { cs.Put(kube.Nodes, kube.Object{Name: "n"}) }, pem, "provider"},
		"a lookalike prefix":       {func(cs *kubetest.Fake) { cs.Put(kube.Nodes, kube.Object{Name: "n", ProviderID: "kind-docker://x"}) }, pem, "provider"},
		"no nodes":                 {func(cs *kubetest.Fake) {}, pem, "no nodes"},
		"nodes cannot be listed":   {func(cs *kubetest.Fake) { kindNodes(cs, "a"); forbid(cs, "list", "nodes") }, pem, "could not read"},
		"no certificate authority": {func(cs *kubetest.Fake) { kindNodes(cs, "a") }, nil, "certificate authority"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			cs := kubetest.New()
			c.build(cs)

			v := verifyWith(cs, c.ca)

			if v.Supported || !strings.Contains(v.Reason, c.reason) {
				t.Fatalf("verdict = %+v, want a refusal mentioning %q", v, c.reason)
			}
		})
	}
}

func TestAnUnreachableClusterIsReportedAsUnreachableNotAsUnsupported(t *testing.T) {
	cs := kubetest.New()
	cs.FailVersion(forbiddenErr("version"))

	v := verifyWith(cs, pem)

	if v.Supported || v.Reachable {
		t.Fatalf("verdict = %+v", v)
	}
}

// ---------------------------------------------------------------- the helper flows use the verdict

func dockerDesktopLoader(cs *kubetest.Fake) *fakeLoader {
	l := newLoader(cs)
	l.contexts = []clusters.Context{{Name: "docker-desktop", Server: "https://127.0.0.1:50345"}}
	l.clients = map[string]*kubetest.Fake{"docker-desktop": cs}
	return l
}

func TestDiscoverSupportsDockerDesktopWhenItsNodesReportKind(t *testing.T) {
	cs := newCluster(time.Hour)
	kindNodes(cs, "desktop-control-plane", "desktop-worker")

	r := Discover(bg, deps(dockerDesktopLoader(cs), answer(approve.Declined), newBackend()))

	c := r.Contexts[0]
	if !c.Supported || c.Reachable == nil || !*c.Reachable || c.Reason != "" || c.Authorization != "absent" {
		t.Fatalf("context = %+v", c)
	}
}

func TestDiscoverDoesNotSupportANamedCandidateWhoseNodesAreNotKind(t *testing.T) {
	cs := newCluster(time.Hour)
	cs.Put(kube.Nodes, kube.Object{Name: "n", ProviderID: "aws:///us-east-1a/i-1"})

	r := Discover(bg, deps(dockerDesktopLoader(cs), answer(approve.Declined), newBackend()))

	c := r.Contexts[0]
	if c.Supported || !strings.Contains(c.Reason, "provider") || c.Reachable == nil || !*c.Reachable {
		t.Fatalf("context = %+v", c)
	}
	if c.Authorization != "" {
		t.Fatalf("the extension's access must not even be inspected on an unverified cluster: %+v", c)
	}
}

func TestAuthorizeWorksForDockerDesktopAfterTheNativeApproval(t *testing.T) {
	cs := newCluster(8 * time.Hour)
	kindNodes(cs, "desktop-control-plane", "desktop-worker")
	p, b := answer(approve.Approved), newBackend()

	r := Authorize(bg, deps(dockerDesktopLoader(cs), p, b), "docker-desktop")

	if !r.OK || r.State != StateConnected || len(p.requests) != 1 {
		t.Fatalf("result = %+v dialogs %d", r, len(p.requests))
	}
	if body := p.requests[0].Body; !strings.Contains(body, "Cluster: docker-desktop") || !strings.Contains(body, "kind") {
		t.Fatalf("the dialog must name the cluster and the evidence it was recognised by:\n%s", body)
	}
}

func TestAuthorizeNeverPromptsOrWritesOnAClusterThatIsNotKind(t *testing.T) {
	cs := newCluster(8 * time.Hour)
	cs.Put(kube.Nodes, kube.Object{Name: "n", ProviderID: "azure:///subscriptions/x"})
	p, b := answer(approve.Approved), newBackend()

	r := Authorize(bg, deps(dockerDesktopLoader(cs), p, b), "docker-desktop")

	if r.OK || r.State != StateUnsupportedContext || len(p.requests) != 0 {
		t.Fatalf("result = %+v dialogs %d", r, len(p.requests))
	}
	assertNoTokenNoWrite(t, cs, b, p)
}

func TestRenewStopsWhenTheClusterNoLongerReportsKind(t *testing.T) {
	cs := approvedCluster(t)
	kindNodes(cs, "n")
	cs.Put(kube.Nodes, kube.Object{Name: "n", ProviderID: "aws:///z/i-1"}) // the context now points elsewhere
	b := managedBackend()

	r := Renew(bg, deps(newLoader(cs), neverPrompt(t), b), ctxName)

	if r.OK || r.State != StateUnsupportedContext {
		t.Fatalf("result = %+v", r)
	}
	assertNoTokenNoWrite(t, cs, b, answer(approve.Declined))
}
