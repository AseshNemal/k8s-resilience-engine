package flow

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/kube"
	"resilience-engine/helper/internal/kube/kubetest"
	"resilience-engine/helper/internal/rbac"
	"resilience-engine/helper/internal/transport"
)

// neverPrompt fails the test if a dialog is shown: renewal must be silent.
func neverPrompt(t *testing.T) *fakePrompter {
	t.Helper()
	return &fakePrompter{decision: approve.Approved}
}

func assertSilent(t *testing.T, p *fakePrompter) {
	t.Helper()
	if len(p.requests) != 0 {
		t.Fatalf("renewal must never show a dialog, showed %d", len(p.requests))
	}
}

// ---------------------------------------------------------------- the happy path

func TestRenewIssuesAFreshTokenSilentlyAndWritesNothingToTheCluster(t *testing.T) {
	cs := approvedCluster(t)
	p, b := neverPrompt(t), managedBackend()
	r := Renew(bg, deps(newLoader(cs), p, b), ctxName)

	if !r.OK || r.State != StateConnected || r.ExpiresAt != "2026-10-19T20:00:00Z" {
		t.Fatalf("result = %+v", r)
	}
	assertSilent(t, p)
	writes, tokens := clusterWrites(cs)
	if len(writes) != 0 {
		t.Fatalf("renewal must make NO RBAC writes, made %v", writes)
	}
	if tokens != 1 {
		t.Fatalf("exactly one token request is expected, got %d", tokens)
	}
	if len(b.installs) != 1 || !receivedToken(b) || b.installs[0].Context != ctxName {
		t.Fatalf("the backend must receive the new credential: %+v", b.installs)
	}
	noLeak(t, r)
}

func TestRenewWorksForAccessLeftByTheOldScriptWithoutLabellingIt(t *testing.T) {
	cs := approvedCluster(t)
	stripLabels(t, cs)
	r := Renew(bg, deps(newLoader(cs), neverPrompt(t), managedBackend()), ctxName)

	if !r.OK {
		t.Fatalf("result = %+v", r)
	}
	if writes, _ := clusterWrites(cs); len(writes) != 0 {
		t.Fatalf("renewal must not even add a label, wrote %v", writes)
	}
}

func TestRenewUsesTheRealGrantedExpiryNotAnAssumedEightHours(t *testing.T) {
	cs := approvedCluster(t)
	cs2 := newCluster(2 * time.Hour) // a cluster that clamps tokens to 2h
	r2, _ := rbac.Inspect(bg, cs2)
	_ = rbac.Apply(bg, cs2, r2)
	_ = cs
	b := managedBackend()
	r := Renew(bg, deps(newLoader(cs2), neverPrompt(t), b), ctxName)

	if r.ExpiresAt != "2026-10-19T14:00:00Z" || b.installs[0].ExpiresAt != t0.Add(2*time.Hour) {
		t.Fatalf("expiry = %s / %v", r.ExpiresAt, b.installs[0].ExpiresAt)
	}
}

// ---------------------------------------------------------------- never recreate revoked access

func assertNoTokenNoWrite(t *testing.T, cs *kubetest.Fake, b *fakeBackend, p *fakePrompter) {
	t.Helper()
	writes, tokens := clusterWrites(cs)
	if len(writes) != 0 || tokens != 0 {
		t.Fatalf("a refused renewal must write nothing and issue no token; wrote %v, token requests %d", writes, tokens)
	}
	if len(b.installs) != 0 {
		t.Fatal("nothing may be delivered to the backend")
	}
	assertSilent(t, p)
}

func TestRevokedAccessIsReportedAndNeverSilentlyRecreated(t *testing.T) {
	cs := approvedCluster(t)
	removeAccess(t, cs) // someone removed the access from the cluster
	p, b := neverPrompt(t), managedBackend()
	r := Renew(bg, deps(newLoader(cs), p, b), ctxName)

	if r.OK || r.State != StateAuthorizationGone || !strings.Contains(r.Message, "approve it again") {
		t.Fatalf("result = %+v", r)
	}
	assertNoTokenNoWrite(t, cs, b, p)
	if rep, _ := rbac.Inspect(bg, cs); rep.Overall() != rbac.OverallAbsent {
		t.Fatalf("the access must stay removed, found %s", rep.Overall())
	}
}

func TestPartiallyRemovedAccessIsAlsoNotRecreated(t *testing.T) {
	cs := approvedCluster(t)
	if err := cs.Delete(bg, kube.ClusterRoleBindings, "", rbac.Name); err != nil {
		t.Fatal(err)
	}
	cs.ClearActions()
	p, b := neverPrompt(t), managedBackend()
	r := Renew(bg, deps(newLoader(cs), p, b), ctxName)

	if r.State != StateAuthorizationGone {
		t.Fatalf("result = %+v", r)
	}
	assertNoTokenNoWrite(t, cs, b, p)
}

func TestTamperedAccessIsRefusedSoNoOverPrivilegedTokenIsEverIssued(t *testing.T) {
	cs := approvedCluster(t)
	widenRole(t, cs) // the ClusterRole now also reads secrets
	p, b := neverPrompt(t), managedBackend()
	r := Renew(bg, deps(newLoader(cs), p, b), ctxName)

	if r.OK || r.State != StateDrifted {
		t.Fatalf("result = %+v", r)
	}
	assertNoTokenNoWrite(t, cs, b, p)
}

func TestARoleApprovedBeforeTheNewReadsIsRefusedWithAnExplanationNotAnAccusation(t *testing.T) {
	cs := approvedCluster(t)
	edit(t, cs, kube.ClusterRoles, "", rbac.Name, func(cr *kube.Object) {
		cr.Rules = []kube.PolicyRule{
			{APIGroups: []string{""}, Resources: []string{"nodes", "pods"}, Verbs: []string{"get", "list"}},
			{APIGroups: []string{"apps"}, Resources: []string{"deployments"}, Verbs: []string{"get", "list"}},
		}
	})
	cs.ClearActions()
	p, b := neverPrompt(t), managedBackend()
	r := Renew(bg, deps(newLoader(cs), p, b), ctxName)

	if r.OK || r.State != StateDrifted {
		t.Fatalf("result = %+v", r)
	}
	if strings.Contains(r.Message, "changed from what you approved") || !strings.Contains(r.Message, "needs") {
		t.Errorf("an upgrade must not be reported as a change someone made: %q", r.Message)
	}
	assertNoTokenNoWrite(t, cs, b, p)
}

func TestAnExtraBindingThatGrantsMoreIsRefused(t *testing.T) {
	cs := approvedCluster(t)
	addExtraBinding(t, cs)
	p, b := neverPrompt(t), managedBackend()
	r := Renew(bg, deps(newLoader(cs), p, b), ctxName)

	if r.OK || r.State != StateOverGranted {
		t.Fatalf("result = %+v", r)
	}
	assertNoTokenNoWrite(t, cs, b, p)
}

func TestTheServiceAccountVanishingBetweenCheckAndTokenIsReportedAsRevoked(t *testing.T) {
	cs := approvedCluster(t)
	cs.Fail("token", "serviceaccounts", notFound("serviceaccounts", rbac.Name))
	b := managedBackend()
	r := Renew(bg, deps(newLoader(cs), neverPrompt(t), b), ctxName)

	if r.State != StateAuthorizationGone || len(b.installs) != 0 {
		t.Fatalf("result = %+v", r)
	}
}

func TestWhenTheUsersKubeconfigCannotIssueTokensTheAnswerIsRBACDenied(t *testing.T) {
	cs := approvedCluster(t)
	cs.Fail("token", "serviceaccounts", forbiddenErr("serviceaccounts/token"))
	r := Renew(bg, deps(newLoader(cs), neverPrompt(t), managedBackend()), ctxName)
	if r.State != StateRBACDenied {
		t.Fatalf("result = %+v", r)
	}
}

// ---------------------------------------------------------------- renewal cannot be used to bypass approval

func TestRenewCannotSetUpAClusterThatWasNeverApproved(t *testing.T) {
	for name, st := range map[string]transport.Status{
		"backend not connected":       {Connected: false},
		"manual (unmanaged) token":    {Connected: true, Managed: false},
		"managed for another cluster": {Connected: true, Managed: true, Context: "kind-other"},
	} {
		t.Run(name, func(t *testing.T) {
			cs := approvedCluster(t) // the cluster even has the access, from the old script
			l := newLoader(cs)
			b := newBackend()
			b.status = st
			p := neverPrompt(t)
			r := Renew(bg, deps(l, p, b), ctxName)

			if r.OK || r.State != StateNotAuthorized {
				t.Fatalf("result = %+v", r)
			}
			if l.called[ctxName] != 0 {
				t.Fatal("the cluster must not even be contacted")
			}
			assertNoTokenNoWrite(t, cs, b, p)
		})
	}
}

func TestRenewRefusesUnsupportedContexts(t *testing.T) {
	cs := approvedCluster(t)
	for _, name := range []string{"docker-desktop", "prod-cluster", "minikube", "nope"} {
		l := newLoader(cs)
		r := Renew(bg, deps(l, neverPrompt(t), managedBackend()), name)
		// docker-desktop here is the kubeadm-style mode on a non-loopback name; a loopback kind
		// docker-desktop renews like any other supported cluster (verify_test.go).
		if r.State != StateUnsupportedContext || l.called[name] != 0 || (name != "nope" && r.Message == "") {
			t.Errorf("%s: %+v (client calls %d)", name, r, l.called[name])
		}
	}
}

// ---------------------------------------------------------------- recovery after restarts

func TestRenewReportsABackendThatIsNotRunningYet(t *testing.T) {
	b := managedBackend()
	b.statusErr = transport.ErrUnavailable
	r := Renew(bg, deps(newLoader(approvedCluster(t)), neverPrompt(t), b), ctxName)
	if r.State != StateBackendUnavailable || !strings.Contains(r.Message, "extension") {
		t.Fatalf("result = %+v", r)
	}
}

func TestRenewReportsAClusterThatHasNotComeBackAfterADockerRestart(t *testing.T) {
	cs := approvedCluster(t)
	cs.FailVersion(errString("connection refused"))
	b := managedBackend()
	r := Renew(bg, deps(newLoader(cs), neverPrompt(t), b), ctxName)

	if r.State != StateClusterUnreachable || len(b.installs) != 0 {
		t.Fatalf("result = %+v", r)
	}
}

func TestRenewSurvivesRepeatedRestartsByReadingEverythingFromTheClusterAndBackend(t *testing.T) {
	// The helper keeps no state of its own: two fresh Deps (a new process each time) behave the same.
	cs := approvedCluster(t)
	b := managedBackend()
	for i := 0; i < 3; i++ {
		r := Renew(bg, deps(newLoader(cs), neverPrompt(t), b), ctxName)
		if !r.OK {
			t.Fatalf("run %d: %+v", i, r)
		}
	}
	if len(b.installs) != 3 {
		t.Fatalf("installs = %d", len(b.installs))
	}
}

func TestRenewOutputNeverContainsCredentialMaterial(t *testing.T) {
	cs := approvedCluster(t)
	var all []Result
	all = append(all, Renew(bg, deps(newLoader(cs), neverPrompt(t), managedBackend()), ctxName))
	widenRole(t, cs)
	all = append(all, Renew(bg, deps(newLoader(cs), neverPrompt(t), managedBackend()), ctxName))
	noLeak(t, all...)
}

// ---------------------------------------------------------------- slow is not down

func TestRenewReportsASlowDockerAsTooSlowNotAsTheBackendBeingDown(t *testing.T) {
	b := managedBackend()
	b.statusErr = fmt.Errorf("%w: %w", transport.ErrUnavailable, context.DeadlineExceeded)
	r := Renew(bg, deps(newLoader(approvedCluster(t)), neverPrompt(t), b), ctxName)

	if r.OK || r.State != StateTooSlow {
		t.Fatalf("result = %+v", r)
	}
	if !strings.Contains(r.Message, "slow") {
		t.Fatalf("the message should say what is slow: %q", r.Message)
	}
}

func TestAnyFailureAfterTheDeadlineIsReportedAsTooSlow(t *testing.T) {
	expired, cancel := context.WithDeadline(bg, time.Now().Add(-time.Second))
	defer cancel()
	for _, state := range []State{StateBackendUnavailable, StateClusterUnreachable, StateError} {
		got := Slow(expired, fail("renew", state, "x"))
		if got.State != StateTooSlow {
			t.Fatalf("%s became %s", state, got.State)
		}
	}
}

func TestSlowLeavesRealAnswersAndSuccessAlone(t *testing.T) {
	expired, cancel := context.WithDeadline(bg, time.Now().Add(-time.Second))
	defer cancel()
	for _, r := range []Result{
		{OK: true, State: StateConnected},
		fail("renew", StateAuthorizationGone, "removed"),
		fail("renew", StateDrifted, "changed"),
	} {
		if got := Slow(expired, r); got.State != r.State {
			t.Fatalf("%s must stay %s, got %s", r.State, r.State, got.State)
		}
	}
	if got := Slow(bg, fail("renew", StateError, "x")); got.State != StateError {
		t.Fatalf("with time left a failure stays itself, got %s", got.State)
	}
}

// ---------------------------------------------------------------- unattended renewal

func backendIn(state string) *fakeBackend {
	b := managedBackend()
	b.status.State = state
	return b
}

func TestRenewManagedDoesNothingWhileTheTokenIsNotDue(t *testing.T) {
	cs, b, p := approvedCluster(t), backendIn("ok"), neverPrompt(t)
	r := RenewManaged(bg, deps(newLoader(cs), p, b))

	if !r.OK || r.State != StateCurrent {
		t.Fatalf("result = %+v", r)
	}
	if _, tokens := clusterWrites(cs); tokens != 0 || len(b.installs) != 0 {
		t.Fatalf("a token that is not due must not be replaced: tokens=%d installs=%d", tokens, len(b.installs))
	}
	assertSilent(t, p)
}

func TestRenewManagedRenewsWhenDueOrExpiredWithoutBeingTold(t *testing.T) {
	for _, state := range []string{"renew-soon", "expired"} {
		cs, b, p := approvedCluster(t), backendIn(state), neverPrompt(t)
		r := RenewManaged(bg, deps(newLoader(cs), p, b))

		if !r.OK || r.State != StateConnected || r.Context != ctxName {
			t.Fatalf("%s: result = %+v", state, r)
		}
		writes, tokens := clusterWrites(cs)
		if len(writes) != 0 || tokens != 1 || !receivedToken(b) {
			t.Fatalf("%s: writes=%v tokens=%d installs=%d", state, writes, tokens, len(b.installs))
		}
		assertSilent(t, p)
		noLeak(t, r)
	}
}

func TestRenewManagedLeavesAnythingNotManagedAlone(t *testing.T) {
	for name, st := range map[string]transport.Status{
		"nothing connected": {Connected: false},
		"added by hand":     {Connected: true, Managed: false, State: "expired"},
	} {
		cs, b := approvedCluster(t), newBackend()
		b.status = st
		r := RenewManaged(bg, deps(newLoader(cs), neverPrompt(t), b))
		if !r.OK || r.State != StateIdle {
			t.Fatalf("%s: result = %+v", name, r)
		}
		if _, tokens := clusterWrites(cs); tokens != 0 || len(b.installs) != 0 {
			t.Fatalf("%s: nothing may be issued or installed", name)
		}
	}
}

func TestRenewManagedStopsAtRemovedOrChangedAccessAndNeverRecreatesIt(t *testing.T) {
	cs := approvedCluster(t)
	removeAccess(t, cs)
	r := RenewManaged(bg, deps(newLoader(cs), neverPrompt(t), backendIn("expired")))

	if r.OK || r.State != StateAuthorizationGone {
		t.Fatalf("result = %+v", r)
	}
	if writes, tokens := clusterWrites(cs); len(writes) != 0 || tokens != 0 {
		t.Fatalf("removed access must stay removed: writes=%v tokens=%d", writes, tokens)
	}
}

func TestRenewManagedReportsASlowDockerAsTooSlow(t *testing.T) {
	b := managedBackend()
	b.statusErr = fmt.Errorf("%w: %w", transport.ErrUnavailable, context.DeadlineExceeded)
	if r := RenewManaged(bg, deps(newLoader(approvedCluster(t)), neverPrompt(t), b)); r.State != StateTooSlow {
		t.Fatalf("result = %+v", r)
	}
}
