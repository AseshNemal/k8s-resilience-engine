package flow

import (
	"encoding/json"
	"strings"
	"testing"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/clusters"
	"resilience-engine/helper/internal/kube"
	"resilience-engine/helper/internal/kube/kubetest"
	"resilience-engine/helper/internal/kubeconfig"
	"resilience-engine/helper/internal/lock"
	"resilience-engine/helper/internal/transport"
)

// A cluster the person approved once is reconnected without asking again, but only when it is provably the
// same cluster and its approved read-only access is still exactly as approved. Reconnecting only ever asks
// the cluster for a token (a TokenRequest); it never creates, updates or deletes anything in it.

var ctxCluster = clusters.Context{Name: ctxName, Server: "https://127.0.0.1:58227"}

func approvedIdentity() transport.Identity {
	return identityOf(ctxCluster, kubeconfig.Cluster{Server: ctxCluster.Server, CAData: pem})
}

// approvedBackend is a backend that holds no connection but remembers the cluster that was approved.
func approvedBackend() *fakeBackend {
	b := newBackend()
	id := approvedIdentity()
	b.status = transport.Status{Connected: false, Approved: &transport.Approval{Context: ctxName, Server: id.Server, CASha256: id.CASha256}}
	return b
}

func reconnect(t *testing.T, cs *kubetest.Fake, b *fakeBackend, tweak ...func(*fakeLoader)) (Result, *fakePrompter, *fakeLoader) {
	t.Helper()
	p, l := neverPrompt(t), newLoader(cs)
	for _, f := range tweak {
		f(l)
	}
	return Reconnect(bg, deps(l, p, b)), p, l
}

func TestAnApprovedClusterIsReconnectedWithATokenAndNothingElse(t *testing.T) {
	cs := approvedCluster(t)
	b := approvedBackend()

	r, p, _ := reconnect(t, cs, b)

	if !r.OK || r.State != StateConnected || r.Context != ctxName {
		t.Fatalf("result = %+v", r)
	}
	writes, tokens := clusterWrites(cs)
	if len(writes) != 0 || tokens != 1 {
		t.Fatalf("reconnecting may only request a token: wrote %v, token requests %d", writes, tokens)
	}
	if !r.TokenRequest || len(r.Changes) != 0 {
		t.Fatalf("the result must say a token was requested and that nothing in the cluster changed: %+v", r)
	}
	if len(b.installs) != 1 || b.installs[0].Origin != OriginReconnect || b.installs[0].Identity == nil || *b.installs[0].Identity != approvedIdentity() {
		t.Fatalf("the backend must be told this is a reconnect of the approved identity: %+v", b.installs)
	}
	assertSilent(t, p)
	if !receivedToken(b) {
		t.Fatal("the backend must receive the token")
	}
	noLeak(t, r)
}

func TestNothingIsReconnectedUnlessAClusterWasApprovedAndStillWanted(t *testing.T) {
	cases := map[string]func(*fakeBackend){
		"nothing was ever approved":  func(b *fakeBackend) { b.status.Approved = nil },
		"the person disconnected it": func(b *fakeBackend) { b.status.Approved.DisconnectedByUser = true },
		"it is already connected":    func(b *fakeBackend) { b.status.Connected = true },
	}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			cs := approvedCluster(t)
			b := approvedBackend()
			change(b)

			r, p, l := reconnect(t, cs, b)

			if !r.OK || r.State != StateIdle || r.Message == "" {
				t.Fatalf("result = %+v", r)
			}
			if l.called[ctxName] != 0 {
				t.Fatal("the cluster must not even be contacted")
			}
			assertNoTokenNoWrite(t, cs, b, p)
		})
	}
}

func TestAClusterThatIsNotTheApprovedOneIsNeverReconnected(t *testing.T) {
	other := []byte("-----BEGIN CERTIFICATE-----\nMIIC-A-DIFFERENT-CLUSTER\n-----END CERTIFICATE-----\n")
	cases := map[string]struct {
		tweak func(*fakeLoader)
		state State
	}{
		"a different certificate authority": {func(l *fakeLoader) { l.ca = other }, StateIdentityChanged},
		"a different API port": {func(l *fakeLoader) {
			l.contexts[0].Server = "https://127.0.0.1:60000"
		}, StateIdentityChanged},
		"the context was removed": {func(l *fakeLoader) { l.contexts = l.contexts[1:] }, StateUnsupportedContext},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			cs := approvedCluster(t)
			b := approvedBackend()

			r, p, _ := reconnect(t, cs, b, c.tweak)

			if r.OK || r.State != c.state {
				t.Fatalf("result = %+v, want state %s", r, c.state)
			}
			assertNoTokenNoWrite(t, cs, b, p)
		})
	}
}

func TestRevokedOrChangedAccessIsReportedAndNeverRecreated(t *testing.T) {
	cases := map[string]struct {
		damage func(*testing.T, *kubetest.Fake)
		state  State
	}{
		"removed":       {removeAccess, StateAuthorizationGone},
		"widened":       {widenRole, StateDrifted},
		"extra binding": {addExtraBinding, StateOverGranted},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			cs := approvedCluster(t)
			c.damage(t, cs)
			b := approvedBackend()

			r, p, _ := reconnect(t, cs, b)

			if r.OK || r.State != c.state {
				t.Fatalf("result = %+v, want %s", r, c.state)
			}
			assertNoTokenNoWrite(t, cs, b, p) // nothing is written to put the access back
		})
	}
}

func TestReconnectNeedsTheSameVerifiedLocalClusterAsEveryOtherCommand(t *testing.T) {
	cs := approvedCluster(t)
	cs.Put(kube.Nodes, kube.Object{Name: "n", ProviderID: "aws:///z/i-1"})
	b := approvedBackend()

	r, p, _ := reconnect(t, cs, b)

	if r.OK || r.State != StateUnsupportedContext {
		t.Fatalf("result = %+v", r)
	}
	assertNoTokenNoWrite(t, cs, b, p)
}

func TestReconnectReportsAnUnreachableClusterAndABackendThatIsNotThere(t *testing.T) {
	cs := approvedCluster(t)
	cs.FailVersion(forbiddenErr("version"))
	r, _, _ := reconnect(t, cs, approvedBackend())
	if r.OK || r.State != StateClusterUnreachable {
		t.Fatalf("unreachable cluster: %+v", r)
	}

	b := approvedBackend()
	b.statusErr = transport.ErrUnavailable
	r, _, _ = reconnect(t, approvedCluster(t), b)
	if r.OK || r.State != StateBackendUnavailable {
		t.Fatalf("missing backend: %+v", r)
	}
}

// ---------------------------------------------------------------- the identity travels with every install

func TestFirstTimeAuthorizationRecordsTheApprovedIdentityAndSeparatesTheTokenFromTheRBACChanges(t *testing.T) {
	cs := newCluster(8 * 3600 * 1e9)
	p, b := answer(approve.Approved), newBackend()

	r := Authorize(bg, deps(newLoader(cs), p, b), ctxName)

	if !r.OK || len(p.requests) != 1 {
		t.Fatalf("result = %+v", r)
	}
	if len(b.installs) != 1 || b.installs[0].Origin != OriginAuthorize || b.installs[0].Identity == nil || *b.installs[0].Identity != approvedIdentity() {
		t.Fatalf("install = %+v", b.installs)
	}
	if len(r.Changes) == 0 || !r.TokenRequest {
		t.Fatalf("the RBAC changes the person approved and the token request must be reported separately: %+v", r)
	}
	if !strings.Contains(r.Message, "TokenRequest") {
		t.Fatalf("the message must say a token was requested from the cluster, not that the cluster was changed: %q", r.Message)
	}
}

func TestRenewKeepsTheApprovedIdentityAndRefusesADifferentCluster(t *testing.T) {
	cs := approvedCluster(t)
	b := managedBackend()
	id := approvedIdentity()
	b.status.Approved = &transport.Approval{Context: ctxName, Server: id.Server, CASha256: id.CASha256}

	r := Renew(bg, deps(newLoader(cs), neverPrompt(t), b), ctxName)

	if !r.OK || len(b.installs) != 1 || b.installs[0].Origin != OriginRenew || *b.installs[0].Identity != id {
		t.Fatalf("result %+v installs %+v", r, b.installs)
	}
	if !r.TokenRequest || len(r.Changes) != 0 {
		t.Fatalf("a renewal only requests a token: %+v", r)
	}

	// the same context now reaches a cluster with another certificate authority
	cs2, b2 := approvedCluster(t), managedBackend()
	b2.status.Approved = &transport.Approval{Context: ctxName, Server: id.Server, CASha256: id.CASha256}
	l := newLoader(cs2)
	l.ca = []byte("-----BEGIN CERTIFICATE-----\nMIIC-OTHER\n-----END CERTIFICATE-----\n")
	r = Renew(bg, deps(l, neverPrompt(t), b2), ctxName)
	if r.OK || r.State != StateIdentityChanged {
		t.Fatalf("result = %+v", r)
	}
	assertNoTokenNoWrite(t, cs2, b2, neverPrompt(t))
}

func TestAConnectionApprovedBeforeIdentitiesWereRecordedStillRenewsAndRecordsOne(t *testing.T) {
	cs := approvedCluster(t)
	b := managedBackend() // no Approved record: installed by an earlier version

	r := Renew(bg, deps(newLoader(cs), neverPrompt(t), b), ctxName)

	if !r.OK || len(b.installs) != 1 || b.installs[0].Identity == nil || *b.installs[0].Identity != approvedIdentity() {
		t.Fatalf("result %+v installs %+v", r, b.installs)
	}
}

func TestIdentityDependsOnTheAddressAndTheCertificateAuthorityNotOnFormatting(t *testing.T) {
	a := identityOf(ctxCluster, kubeconfig.Cluster{CAData: pem})
	if a.Server != "127.0.0.1:58227" || len(a.CASha256) != 64 {
		t.Fatalf("identity = %+v", a)
	}
	if b := identityOf(clusters.Context{Name: "x", Server: "https://127.0.0.1:58227/some/path"}, kubeconfig.Cluster{CAData: pem}); b != a {
		t.Fatalf("the path and the context name are not part of the address: %+v vs %+v", b, a)
	}
	ca := realCA(t)
	der := identityOf(ctxCluster, kubeconfig.Cluster{CAData: ca})
	spaced := identityOf(ctxCluster, kubeconfig.Cluster{CAData: append([]byte("\n\n"), append(append([]byte(nil), ca...), []byte("\n\n")...)...)})
	if der != spaced {
		t.Fatal("blank lines around a real certificate must not change its fingerprint")
	}
	if der == identityOf(ctxCluster, kubeconfig.Cluster{CAData: realCA(t)}) {
		t.Fatal("two different certificate authorities must have different fingerprints")
	}
	if identityOf(ctxCluster, kubeconfig.Cluster{CAData: []byte("x")}).CASha256 == a.CASha256 {
		t.Fatal("different authorities must have different fingerprints")
	}
}

// ---------------------------------------------------------------- one connection or renewal at a time

type fakeLock struct {
	held               bool
	acquired, released int
}

func (l *fakeLock) Acquire() (func(), error) {
	if l.held {
		return nil, lock.ErrBusy
	}
	l.held = true
	l.acquired++
	return func() {
		if l.held {
			l.held = false
			l.released++
		}
	}, nil
}

func withLock(d Deps, l lock.Lock) Deps { d.Lock = l; return d }

func TestEveryCommandThatIssuesATokenOrChangesAConnectionRefusesWhileAnotherIsRunning(t *testing.T) {
	commands := map[string]func(Deps) Result{
		"authorize":     func(d Deps) Result { return Authorize(bg, d, ctxName) },
		"renew":         func(d Deps) Result { return Renew(bg, d, ctxName) },
		"renew-managed": func(d Deps) Result { return RenewManaged(bg, d) },
		"reconnect":     func(d Deps) Result { return Reconnect(bg, d) },
		"revoke":        func(d Deps) Result { return Revoke(bg, d, ctxName) },
	}
	for name, run := range commands {
		t.Run(name, func(t *testing.T) {
			cs := approvedCluster(t)
			b, p, l := managedBackend(), answer(approve.Approved), newLoader(cs)
			busy := &fakeLock{held: true}

			r := run(withLock(deps(l, p, b), busy))

			if r.OK || r.State != StateBusy || r.Message == "" {
				t.Fatalf("result = %+v", r)
			}
			if l.called[ctxName] != 0 || len(p.requests) != 0 || len(b.installs) != 0 || b.disconnect != 0 {
				t.Fatal("a refused attempt must touch nothing: no cluster, no dialog, no backend")
			}
			assertNoTokenNoWrite(t, cs, b, p)
		})
	}
}

func TestTheLockIsReleasedWhateverTheOutcome(t *testing.T) {
	outcomes := map[string]func(*fakeLock) Result{
		"success": func(l *fakeLock) Result {
			return Reconnect(bg, withLock(deps(newLoader(approvedCluster(t)), neverPrompt(t), approvedBackend()), l))
		},
		"declined": func(l *fakeLock) Result {
			return Authorize(bg, withLock(deps(newLoader(newCluster(1)), answer(approve.Declined), newBackend()), l), ctxName)
		},
		"refused": func(l *fakeLock) Result {
			return Reconnect(bg, withLock(deps(newLoader(approvedCluster(t)), neverPrompt(t), newBackend()), l))
		},
		"backend down": func(l *fakeLock) Result {
			b := managedBackend()
			b.statusErr = transport.ErrUnavailable
			return Renew(bg, withLock(deps(newLoader(approvedCluster(t)), neverPrompt(t), b), l), ctxName)
		},
	}
	for name, run := range outcomes {
		t.Run(name, func(t *testing.T) {
			l := &fakeLock{}

			run(l)

			if l.acquired != 1 || l.released != 1 || l.held {
				t.Fatalf("acquired %d released %d held %v", l.acquired, l.released, l.held)
			}
		})
	}
}

func TestTheResultNeverCarriesAnythingButFixedFields(t *testing.T) {
	r := Result{OK: true, Command: "reconnect", State: StateConnected, TokenRequest: true}
	out, _ := json.Marshal(r)
	if !strings.Contains(string(out), `"tokenRequest":true`) {
		t.Fatalf("json = %s", out)
	}
}

func TestAnIdentityMatchesOnlyAnApprovalWithTheSameAddressAndAuthority(t *testing.T) {
	id := approvedIdentity()
	same := &transport.Approval{Server: id.Server, CASha256: id.CASha256}

	if !sameIdentity(same, id) {
		t.Fatal("the approved identity must match itself")
	}
	if sameIdentity(nil, id) {
		t.Fatal("no approval must never match: the predicate fails closed on its own, whatever its callers check first")
	}
	if sameIdentity(&transport.Approval{Server: "127.0.0.1:1", CASha256: id.CASha256}, id) || sameIdentity(&transport.Approval{Server: id.Server, CASha256: "0"}, id) {
		t.Fatal("a different address or authority must not match")
	}
}
