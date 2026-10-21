package flow

import (
	"errors"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/agent"
	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/transport"
)

// ---------------------------------------------------------------- authorize sets the job up

func TestAuthorizeDisclosesTheBackgroundJobInTheDialogAndInstallsItAfterApproval(t *testing.T) {
	a, p := macAgent(), answer(approve.Approved)
	r := Authorize(bg, withAgent(deps(newLoader(newCluster(8*time.Hour)), p, newBackend()), a), ctxName)

	if !r.OK || r.Background != string(agent.On) || a.installs != 1 {
		t.Fatalf("result = %+v installs=%d", r, a.installs)
	}
	dialog := p.requests[0].Title + "\n" + p.requests[0].Body
	for _, want := range []string{"background", "a test background job", "15 minutes", "never changes the cluster"} {
		if !strings.Contains(dialog, want) {
			t.Errorf("the dialog must disclose the background job; lacks %q:\n%s", want, dialog)
		}
	}
}

func TestAuthorizeInstallsNothingInTheBackgroundWhenDeclined(t *testing.T) {
	a := macAgent()
	r := Authorize(bg, withAgent(deps(newLoader(newCluster(8*time.Hour)), answer(approve.Declined), newBackend()), a), ctxName)
	if r.OK || a.installs != 0 {
		t.Fatalf("declined must change nothing: %+v installs=%d", r, a.installs)
	}
}

func TestAuthorizeWithoutAVerifiedBackgroundJobSaysNothingAboutOne(t *testing.T) {
	p := answer(approve.Approved)
	r := Authorize(bg, withAgent(deps(newLoader(newCluster(8*time.Hour)), p, newBackend()), &fakeAgent{supported: false}), ctxName)
	if !r.OK || r.Background != string(agent.Unsupported) {
		t.Fatalf("result = %+v", r)
	}
	if strings.Contains(p.requests[0].Body, "a test background job") {
		t.Fatalf("the dialog must not promise a job this system cannot have:\n%s", p.requests[0].Body)
	}
}

func TestAConnectionStaysConnectedWhenOnlyTheBackgroundJobFails(t *testing.T) {
	a := macAgent()
	a.installErr = errors.New("launchctl said no")
	r := Authorize(bg, withAgent(deps(newLoader(newCluster(8*time.Hour)), answer(approve.Approved), newBackend()), a), ctxName)
	if !r.OK || r.State != StateConnected || r.Background != string(agent.Off) {
		t.Fatalf("the credential is installed, so the connection is good: %+v", r)
	}
	if !strings.Contains(r.Message, "background") {
		t.Fatalf("the message must say the job was not set up: %q", r.Message)
	}
}

// ---------------------------------------------------------------- enabling it for an existing connection

func TestEnableBackgroundAsksOnceAndInstallsOnlyOnApproval(t *testing.T) {
	a, p := macAgent(), answer(approve.Approved)
	r := EnableBackground(bg, withAgent(deps(newLoader(approvedCluster(t)), p, managedBackend()), a), ctxName)

	if !r.OK || r.Background != string(agent.On) || a.installs != 1 || len(p.requests) != 1 {
		t.Fatalf("result = %+v installs=%d dialogs=%d", r, a.installs, len(p.requests))
	}
	if !strings.Contains(p.requests[0].Body, ctxName) || !strings.Contains(p.requests[0].Body, "a test background job") {
		t.Fatalf("the dialog must name the cluster and the job:\n%s", p.requests[0].Body)
	}
}

func TestEnableBackgroundDoesNothingWhenDeclinedOrUnanswered(t *testing.T) {
	for _, d := range []approve.Decision{approve.Declined, approve.TimedOut} {
		a := macAgent()
		r := EnableBackground(bg, withAgent(deps(newLoader(approvedCluster(t)), answer(d), managedBackend()), a), ctxName)
		if r.OK || a.installs != 0 {
			t.Fatalf("%s: result = %+v installs=%d", d, r, a.installs)
		}
	}
}

func TestEnableBackgroundNeedsAnApprovedConnectionAndAVerifiedSystem(t *testing.T) {
	p := answer(approve.Approved)
	notManaged := newBackend()
	notManaged.status = transport.Status{Connected: true, Managed: false}
	for name, tc := range map[string]struct {
		a *fakeAgent
		b *fakeBackend
		s State
	}{
		"never approved":    {macAgent(), notManaged, StateNotAuthorized},
		"unverified system": {&fakeAgent{supported: false}, managedBackend(), StateBackgroundUnsupported},
	} {
		r := EnableBackground(bg, withAgent(deps(newLoader(approvedCluster(t)), p, tc.b), tc.a), ctxName)
		if r.OK || r.State != tc.s || tc.a.installs != 0 {
			t.Errorf("%s: result = %+v", name, r)
		}
	}
	if len(p.requests) != 0 {
		t.Fatalf("no dialog may be shown when the answer is already no, shown %d", len(p.requests))
	}
}

func TestEnableBackgroundReportsAJobThatCouldNotBeLoaded(t *testing.T) {
	a := macAgent()
	a.installErr = agent.ErrNotLoaded
	r := EnableBackground(bg, withAgent(deps(newLoader(approvedCluster(t)), answer(approve.Approved), managedBackend()), a), ctxName)
	if r.OK || r.State != StateError || r.Background != string(agent.Off) {
		t.Fatalf("result = %+v", r)
	}
}

// ---------------------------------------------------------------- reading and removing it

func TestBackgroundStatusOnlyReads(t *testing.T) {
	a, p := macAgent(), answer(approve.Approved)
	a.state = agent.Stale
	r := BackgroundStatus(bg, withAgent(deps(newLoader(approvedCluster(t)), p, managedBackend()), a))
	if !r.OK || r.Background != string(agent.Stale) || a.installs+a.uninstalls != 0 || len(p.requests) != 0 {
		t.Fatalf("result = %+v", r)
	}
}

func TestDisableBackgroundRemovesTheJobWithoutAskingBecauseRemovingIsAlwaysSafe(t *testing.T) {
	a, p := macAgent(), answer(approve.Approved)
	a.state = agent.On
	r := DisableBackground(bg, withAgent(deps(newLoader(approvedCluster(t)), p, managedBackend()), a))
	if !r.OK || r.Background != string(agent.Off) || a.uninstalls != 1 || len(p.requests) != 0 {
		t.Fatalf("result = %+v", r)
	}
}

func TestRevokingTheAccessAlsoRemovesTheBackgroundJob(t *testing.T) {
	a := macAgent()
	a.state = agent.On
	r := Revoke(bg, withAgent(deps(newLoader(approvedCluster(t)), answer(approve.Approved), managedBackend()), a), ctxName)
	if !r.OK || a.uninstalls != 1 || r.Background != string(agent.Off) {
		t.Fatalf("result = %+v uninstalls=%d", r, a.uninstalls)
	}
}

func TestDecliningARevokeKeepsTheBackgroundJob(t *testing.T) {
	a := macAgent()
	a.state = agent.On
	Revoke(bg, withAgent(deps(newLoader(approvedCluster(t)), answer(approve.Declined), managedBackend()), a), ctxName)
	if a.uninstalls != 0 {
		t.Fatal("nothing was removed, so the job must stay")
	}
}

func TestRenewalNeverInstallsOrRemovesTheBackgroundJob(t *testing.T) {
	a := macAgent()
	d := withAgent(deps(newLoader(approvedCluster(t)), neverPrompt(t), managedBackend()), a)
	Renew(bg, d, ctxName)
	RenewManaged(bg, d)
	if a.installs+a.uninstalls != 0 {
		t.Fatal("renewal must not change the background job")
	}
}
