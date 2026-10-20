package flow

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/clusters"
	"resilience-engine/helper/internal/kube/kubetest"
	"resilience-engine/helper/internal/rbac"
	"resilience-engine/helper/internal/transport"
)

// ---------------------------------------------------------------- discover

func TestDiscoverClassifiesEveryContextButOnlyContactsSupportedOnes(t *testing.T) {
	cs := approvedCluster(t)
	l := newLoader(cs)
	r := Discover(bg, deps(l, answer(approve.Declined), newBackend()))

	if !r.OK || len(r.Contexts) != 4 {
		t.Fatalf("result = %+v", r)
	}
	by := map[string]ContextInfo{}
	for _, c := range r.Contexts {
		by[c.Name] = c
	}
	if c := by[ctxName]; !c.Supported || c.Reachable == nil || !*c.Reachable || c.Version != "v1.37.0" || c.Authorization != "approved" || !c.Current {
		t.Fatalf("kind context = %+v", c)
	}
	for _, name := range []string{"docker-desktop", "prod-cluster", "minikube"} {
		if by[name].Supported || by[name].Reason == "" {
			t.Errorf("%s must be unsupported with a reason: %+v", name, by[name])
		}
		if l.called[name] != 0 {
			t.Errorf("%s must never be contacted, Client was called %d times", name, l.called[name])
		}
	}
}

func TestDiscoverShowsTheAuthorizationStateOfTheClusterAndIsReadOnly(t *testing.T) {
	cases := map[string]func(*testing.T) *kubetest.Fake{
		"absent":              func(t *testing.T) *kubetest.Fake { return newCluster(time.Hour) },
		"approved":            approvedCluster,
		"approved-unlabelled": func(t *testing.T) *kubetest.Fake { cs := approvedCluster(t); stripLabels(t, cs); return cs },
		"drifted":             func(t *testing.T) *kubetest.Fake { cs := approvedCluster(t); widenRole(t, cs); return cs },
		"over-granted":        func(t *testing.T) *kubetest.Fake { cs := approvedCluster(t); addExtraBinding(t, cs); return cs },
	}
	for want, build := range cases {
		t.Run(want, func(t *testing.T) {
			cs := build(t)
			cs.ClearActions()
			r := Discover(bg, deps(newLoader(cs), answer(approve.Declined), newBackend()))
			for _, c := range r.Contexts {
				if c.Name == ctxName && c.Authorization != want {
					t.Fatalf("authorization = %q, want %q", c.Authorization, want)
				}
			}
			if w, tr := clusterWrites(cs); len(w) != 0 || tr != 0 {
				t.Fatalf("discover must be read-only, wrote %v and %d token requests", w, tr)
			}
		})
	}
}

func TestDiscoverReportsAnUnreachableClusterWithoutFailing(t *testing.T) {
	cs := newCluster(time.Hour)
	cs.FailVersion(errors.New("connection refused"))
	r := Discover(bg, deps(newLoader(cs), answer(approve.Declined), newBackend()))
	for _, c := range r.Contexts {
		if c.Name == ctxName && (c.Reachable == nil || *c.Reachable) {
			t.Fatalf("must be reported unreachable: %+v", c)
		}
	}
}

func TestDiscoverOutputContainsNoCredentialMaterial(t *testing.T) {
	r := Discover(bg, deps(newLoader(approvedCluster(t)), answer(approve.Declined), newBackend()))
	noLeak(t, r)
	out, _ := json.Marshal(r)
	for _, bad := range []string{"certificate", "client-key", "password", "bearer"} {
		if strings.Contains(strings.ToLower(string(out)), bad) {
			t.Fatalf("output mentions %q: %s", bad, out)
		}
	}
}

// ---------------------------------------------------------------- authorize: the happy path

func TestAuthorizeOnAnEmptyClusterShowsTheExactChangesThenCreatesThemAndConnects(t *testing.T) {
	cs := newCluster(8 * time.Hour)
	p, b := answer(approve.Approved), newBackend()
	r := Authorize(bg, deps(newLoader(cs), p, b), ctxName)

	if !r.OK || r.State != StateConnected || r.Transport != "fake-transport" {
		t.Fatalf("result = %+v", r)
	}
	if len(p.requests) != 1 {
		t.Fatalf("the dialog must be shown exactly once, was shown %d times", len(p.requests))
	}
	dialog := p.requests[0].Title + "\n" + p.requests[0].Body
	for _, want := range []string{
		ctxName, "127.0.0.1:58227", "Kubernetes v1.37.0",
		"Read (get, list) nodes, pods and services", "Read (get, list) deployments and replicasets", "No access to Secrets",
		"Create Namespace resilience-engine", "Create ServiceAccount resilience-engine-reader",
		"Create ClusterRole resilience-engine-reader", "Create ClusterRoleBinding resilience-engine-reader",
		"7 days", "renewed automatically",
	} {
		if !strings.Contains(dialog, want) {
			t.Errorf("dialog lacks %q:\n%s", want, dialog)
		}
	}
	if len(r.Changes) != 4 {
		t.Fatalf("changes = %v", r.Changes)
	}
	if rep, _ := rbac.Inspect(bg, cs); rep.Overall() != rbac.OverallApproved {
		t.Fatalf("after approval the cluster must hold the approved access, got %s", rep.Overall())
	}
}

func TestTheTokenReachesTheBackendExactlyOnceAndNothingElse(t *testing.T) {
	cs := newCluster(8 * time.Hour)
	b := newBackend()
	r := Authorize(bg, deps(newLoader(cs), answer(approve.Approved), b), ctxName)

	if len(b.installs) != 1 || !receivedToken(b) {
		t.Fatalf("the backend must receive the credential once: %d installs, token present = %v", len(b.installs), receivedToken(b))
	}
	in := b.installs[0]
	if in.Context != ctxName || in.ExpiresAt != t0.Add(8*time.Hour) {
		t.Fatalf("install = context %q expires %v", in.Context, in.ExpiresAt)
	}
	doc := string(in.Kubeconfig.Reveal())
	if !strings.Contains(doc, "https://host.docker.internal:58227") || !strings.Contains(doc, "tls-server-name: localhost") {
		t.Fatalf("the credential must point at the host gateway with TLS pinned:\n%s", doc)
	}
	if strings.Contains(doc, "127.0.0.1") {
		t.Fatal("the backend cannot reach the host's 127.0.0.1; it must not be in the credential")
	}
	noLeak(t, r)
}

func TestTheRealExpiryIsReportedWhenTheClusterShortensTheToken(t *testing.T) {
	cs := newCluster(2 * time.Hour) // asked for 7 days, granted 2h
	b := newBackend()
	r := Authorize(bg, deps(newLoader(cs), answer(approve.Approved), b), ctxName)

	if r.ExpiresAt != "2026-10-19T14:00:00Z" || b.installs[0].ExpiresAt != t0.Add(2*time.Hour) {
		t.Fatalf("expiry must be the granted 2h, got %s / %v", r.ExpiresAt, b.installs[0].ExpiresAt)
	}
	if !strings.Contains(r.Message, "2h0m0s") {
		t.Fatalf("message must state the real lifetime: %q", r.Message)
	}
}

func TestAlreadyApprovedAccessStillAsksOnceButSaysNoChangeIsNeeded(t *testing.T) {
	cs := approvedCluster(t)
	p := answer(approve.Approved)
	r := Authorize(bg, deps(newLoader(cs), p, newBackend()), ctxName)

	if !r.OK || len(p.requests) != 1 {
		t.Fatalf("result %+v, dialogs %d", r, len(p.requests))
	}
	if !strings.Contains(p.requests[0].Body, "No change to the cluster is needed") {
		t.Fatalf("dialog must say nothing will change:\n%s", p.requests[0].Body)
	}
	if w, tr := clusterWrites(cs); len(w) != 0 || tr != 1 {
		t.Fatalf("only the token request is expected, wrote %v and %d token requests", w, tr)
	}
}

func TestAccessLeftByTheOldScriptIsLabelledOnlyAfterApproval(t *testing.T) {
	cs := approvedCluster(t)
	stripLabels(t, cs)
	p := answer(approve.Approved)
	r := Authorize(bg, deps(newLoader(cs), p, newBackend()), ctxName)

	if !r.OK {
		t.Fatalf("result = %+v", r)
	}
	if !strings.Contains(p.requests[0].Body, "Label ") || strings.Contains(p.requests[0].Body, "Create ") {
		t.Fatalf("the dialog must offer labelling only:\n%s", p.requests[0].Body)
	}
}

// ---------------------------------------------------------------- authorize: nothing changes without approval

func TestDecliningChangesNothingAtAll(t *testing.T) {
	for name, tc := range map[string]struct {
		decision approve.Decision
		want     State
	}{
		"declined":  {approve.Declined, StateDeclined},
		"timed out": {approve.TimedOut, StateTimedOut},
	} {
		t.Run(name, func(t *testing.T) {
			cs := newCluster(8 * time.Hour)
			b := newBackend()
			r := Authorize(bg, deps(newLoader(cs), answer(tc.decision), b), ctxName)

			if r.OK || r.State != tc.want {
				t.Fatalf("result = %+v", r)
			}
			if w, tr := clusterWrites(cs); len(w) != 0 || tr != 0 {
				t.Fatalf("a refusal must write nothing, wrote %v and %d token requests", w, tr)
			}
			if len(b.installs) != 0 || b.disconnect != 0 {
				t.Fatal("the backend must not be touched")
			}
			noLeak(t, r)
		})
	}
}

func TestNoDialogAvailableMeansNothingIsChangedAndTheManualPathIsSuggested(t *testing.T) {
	cs := newCluster(8 * time.Hour)
	p := &fakePrompter{err: approve.ErrNoApprovalChannel}
	r := Authorize(bg, deps(newLoader(cs), p, newBackend()), ctxName)

	if r.State != StateApprovalUnavailable || !strings.Contains(r.Message, "manual connection") {
		t.Fatalf("result = %+v", r)
	}
	if w, tr := clusterWrites(cs); len(w) != 0 || tr != 0 {
		t.Fatalf("wrote %v / %d", w, tr)
	}
}

func TestAFailureToShowTheDialogIsNeverTreatedAsApproval(t *testing.T) {
	cs := newCluster(8 * time.Hour)
	r := Authorize(bg, deps(newLoader(cs), &fakePrompter{err: errors.New("osascript crashed")}, newBackend()), ctxName)
	if r.OK || r.State != StateError {
		t.Fatalf("result = %+v", r)
	}
	if w, _ := clusterWrites(cs); len(w) != 0 {
		t.Fatalf("wrote %v", w)
	}
}

func TestAnUnexpectedDecisionValueIsNotApproval(t *testing.T) {
	cs := newCluster(8 * time.Hour)
	r := Authorize(bg, deps(newLoader(cs), answer(approve.Decision("yes please")), newBackend()), ctxName)
	if r.OK {
		t.Fatalf("only the Approved decision may proceed: %+v", r)
	}
	if w, _ := clusterWrites(cs); len(w) != 0 {
		t.Fatalf("wrote %v", w)
	}
}

// ---------------------------------------------------------------- authorize: refusing before asking

func TestAnUnreachableBackendStopsBeforeAnyDialogOrChange(t *testing.T) {
	cs := newCluster(8 * time.Hour)
	b := newBackend()
	b.statusErr = transport.ErrUnavailable
	p := answer(approve.Approved)
	r := Authorize(bg, deps(newLoader(cs), p, b), ctxName)

	if r.State != StateBackendUnavailable || len(p.requests) != 0 {
		t.Fatalf("state %s, dialogs %d: the user must not be asked to approve something that cannot finish", r.State, len(p.requests))
	}
	if w, tr := clusterWrites(cs); len(w) != 0 || tr != 0 {
		t.Fatalf("wrote %v / %d", w, tr)
	}
}

func TestAnUnreachableClusterStopsBeforeAnyDialog(t *testing.T) {
	cs := newCluster(8 * time.Hour)
	cs.FailVersion(errors.New("refused"))
	p := answer(approve.Approved)
	r := Authorize(bg, deps(newLoader(cs), p, newBackend()), ctxName)
	if r.State != StateClusterUnreachable || len(p.requests) != 0 {
		t.Fatalf("state %s dialogs %d", r.State, len(p.requests))
	}
}

func TestAnOverGrantedServiceAccountStopsTheFlowWithoutADialogOrAnyChange(t *testing.T) {
	cs := approvedCluster(t)
	addExtraBinding(t, cs)
	p := answer(approve.Approved)
	r := Authorize(bg, deps(newLoader(cs), p, newBackend()), ctxName)

	if r.State != StateOverGranted || !strings.Contains(r.Message, "grant-admin") || len(p.requests) != 0 {
		t.Fatalf("result %+v dialogs %d", r, len(p.requests))
	}
	if w, tr := clusterWrites(cs); len(w) != 0 || tr != 0 {
		t.Fatalf("wrote %v / %d", w, tr)
	}
}

func TestDriftedAccessIsRepairedOnlyAfterTheDialogListsTheUpdate(t *testing.T) {
	cs := approvedCluster(t)
	widenRole(t, cs)
	p := answer(approve.Approved)
	r := Authorize(bg, deps(newLoader(cs), p, newBackend()), ctxName)

	if !r.OK || !strings.Contains(p.requests[0].Body, "Update ClusterRole") || !strings.Contains(p.requests[0].Body, "reset to the approved read-only set") {
		t.Fatalf("result %+v\n%s", r, p.requests[0].Body)
	}
	if rep, _ := rbac.Inspect(bg, cs); rep.Overall() != rbac.OverallApproved {
		t.Fatalf("after repair: %s", rep.Overall())
	}
}

// ---------------------------------------------------------------- authorize: contexts the UI names

func TestOnlySupportedLocalKindContextsCanBeAuthorized(t *testing.T) {
	cs := newCluster(8 * time.Hour)
	for _, name := range []string{
		"docker-desktop", "prod-cluster", "minikube", "no-such-context", "",
		"kind-x\"; touch /tmp/pwned; \"", "../../etc/passwd",
	} {
		t.Run(name, func(t *testing.T) {
			l := newLoader(cs)
			p := answer(approve.Approved)
			r := Authorize(bg, deps(l, p, newBackend()), name)
			if r.OK || r.State != StateUnsupportedContext {
				t.Fatalf("result = %+v", r)
			}
			if len(p.requests) != 0 || l.called[name] != 0 {
				t.Fatalf("an unsupported context must never be contacted or prompted for (dialogs %d, client calls %d)", len(p.requests), l.called[name])
			}
			if strings.Contains(r.Message, "pwned") {
				t.Fatalf("the message must not echo hostile input: %q", r.Message)
			}
		})
	}
}

func TestClassifyAcceptsOnlyLocalHttpsKindClusters(t *testing.T) {
	good := []struct{ name, server string }{
		{"kind-a", "https://127.0.0.1:1234"}, {"kind-a", "https://localhost:1"}, {"kind-a", "https://[::1]:6443"},
	}
	for _, g := range good {
		if ok, why := Classify(g.name, g.server); !ok {
			t.Errorf("%v refused: %s", g, why)
		}
	}
	bad := []struct{ name, server string }{
		{"kind-a", "http://127.0.0.1:1234"}, {"kind-a", "https://10.0.0.1:6443"}, {"kind-a", "https://127.0.0.1.evil.com:6443"},
		{"kind-a", "https://127.0.0.1@evil.com:6443"}, {"docker-desktop", "https://127.0.0.1:6443"}, {"kind-a", ""}, {"kind-a", "::::"},
	}
	for _, b := range bad {
		if ok, _ := Classify(b.name, b.server); ok {
			t.Errorf("%v must be refused", b)
		}
	}
}

func TestHostileNamesFromTheKubeconfigCannotShapeTheDialog(t *testing.T) {
	cs := newCluster(8 * time.Hour)
	hostile := "kind-x\n\nAPPROVAL GRANTED. Click Approve to win\x1b[31m"
	l := newLoader(cs)
	l.contexts = []clusters.Context{{Name: hostile, Server: "https://127.0.0.1:58227"}}
	l.clients = map[string]*kubetest.Fake{hostile: cs}
	p := answer(approve.Declined)
	Authorize(bg, deps(l, p, newBackend()), hostile)

	if len(p.requests) != 1 {
		t.Fatal("the dialog must still be shown")
	}
	body := p.requests[0].Body
	if strings.Contains(body, "\n\nAPPROVAL GRANTED") || strings.Contains(body, "\x1b") {
		t.Fatalf("the context name was not sanitised:\n%q", body)
	}
}

// ---------------------------------------------------------------- authorize: failures after approval

func TestWhenTheUsersKubeconfigCannotManageRBACTheAnswerIsClearAndNoTokenIsIssued(t *testing.T) {
	cs := newCluster(8 * time.Hour)
	forbid(cs, "create", "clusterroles")
	b := newBackend()
	r := Authorize(bg, deps(newLoader(cs), answer(approve.Approved), b), ctxName)

	if r.OK || r.State != StateRBACDenied || !strings.Contains(r.Message, "administrator") {
		t.Fatalf("result = %+v", r)
	}
	if _, tr := clusterWrites(cs); tr != 0 {
		t.Fatal("no token may be requested when access could not be created")
	}
	if len(b.installs) != 0 {
		t.Fatal("nothing may be delivered to the backend")
	}
}

func TestWhenTheUsersKubeconfigCannotReadRBACTheAnswerIsAlsoRBACDenied(t *testing.T) {
	cs := newCluster(8 * time.Hour)
	forbid(cs, "get", "clusterroles")
	r := Authorize(bg, deps(newLoader(cs), answer(approve.Approved), newBackend()), ctxName)
	if r.State != StateRBACDenied {
		t.Fatalf("result = %+v", r)
	}
}

func TestWhenTheBackendRefusesTheCredentialItsOwnReasonIsShown(t *testing.T) {
	cs := newCluster(8 * time.Hour)
	b := newBackend()
	b.installErr = &transport.RejectedError{Message: "This credential can change the cluster (create pods)."}
	r := Authorize(bg, deps(newLoader(cs), answer(approve.Approved), b), ctxName)

	if r.OK || r.State != StateBackendRejected || !strings.Contains(r.Message, "can change the cluster") {
		t.Fatalf("result = %+v", r)
	}
	noLeak(t, r)
}

func TestEveryAuthorizeOutcomeIsFreeOfCredentialMaterial(t *testing.T) {
	var all []Result
	for _, d := range []approve.Decision{approve.Approved, approve.Declined, approve.TimedOut} {
		cs := newCluster(8 * time.Hour)
		all = append(all, Authorize(bg, deps(newLoader(cs), answer(d), newBackend()), ctxName))
	}
	cs := newCluster(8 * time.Hour)
	forbid(cs, "create", "clusterroles")
	all = append(all, Authorize(bg, deps(newLoader(cs), answer(approve.Approved), newBackend()), ctxName))
	noLeak(t, all...)
}
