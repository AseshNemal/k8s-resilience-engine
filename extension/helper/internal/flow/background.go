package flow

import (
	"context"
	"errors"
	"fmt"
	"time"

	"resilience-engine/helper/internal/agent"
	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/clusters"
)

// The background job keeps the credential renewed when nobody has the extension open (see package
// agent). Three rules keep it inside the same safety model as the rest of the helper:
//
//   - it is only ever installed after the person approved it in a native dialog that says what it is
//     (authorize shows it in the setup dialog; EnableBackground has its own dialog for a connection
//     that was set up earlier);
//   - the job runs renew-managed, which has every restriction of a manual renewal;
//   - removing access removes the job, and removing the job needs no dialog because it only reduces
//     what the helper can do.

// backgroundNote is the paragraph for the setup dialog, or "" when this system has no background job.
func (d Deps) backgroundNote() string {
	if !d.agentSupported() {
		return ""
	}
	return d.backgroundParagraph()
}

// agentSupported reports whether this system has a verified background job.
func (d Deps) agentSupported() bool { return d.Agent != nil && d.Agent.Supported() }

// startBackground installs the job after a successful authorize. The connection is already good by
// then, so a failure here is reported but never turns a working connection into a failed one.
func (d Deps) startBackground(ctx context.Context, r Result) Result {
	if d.Agent == nil {
		return r
	}
	if !d.Agent.Supported() {
		r.Background = string(agent.Unsupported)
		return r
	}
	if err := d.Agent.Install(ctx); err != nil {
		r.Background = string(agent.Off)
		r.Message += "; the background renewal could not be set up, so the token renews only while the extension is open (you can turn it on from the extension)"
		return r
	}
	r.Background = string(agent.On)
	return r
}

// stopBackground removes the job. It runs when access is removed, so a job can never outlive the
// connection it renews.
func (d Deps) stopBackground(ctx context.Context, r Result) Result {
	if !d.agentSupported() {
		return r
	}
	if err := d.Agent.Uninstall(ctx); err != nil {
		r.Background = string(agent.Stale)
		r.Message += " The background renewal job could not be removed; run `resilience-helper agent-disable`."
		return r
	}
	r.Background = string(agent.Off)
	return r
}

func enableDialog(c clusters.Context, paragraph string) approve.Request {
	name := approve.Sanitize(c.Name, 80)
	body := fmt.Sprintf("Cluster: %s\n\nThe extension's cluster token is renewed automatically while the extension is open.\n\n%s", name, paragraph)
	return approve.Request{Title: "Resilience Engine: keep access renewed in the background?", Body: capText(body, 1800), Timeout: 90 * time.Second}
}

// EnableBackground sets the job up for a connection that was approved earlier (before the job
// existed, or when it could not be installed). It needs an existing managed credential for that
// cluster, so it cannot be used to set anything up for a cluster that was never approved.
func EnableBackground(ctx context.Context, d Deps, name string) Result {
	const cmd = "agent-enable"
	if !d.agentSupported() {
		return fail(cmd, StateBackgroundUnsupported, "background renewal is not available on this operating system yet")
	}
	c, refusal := d.supported(cmd, name)
	if refusal != nil {
		return *refusal
	}
	_, st, err := d.pickBackend(ctx)
	if err != nil {
		return backendFailure(cmd, err)
	}
	if !st.Managed || st.Context != c.Name {
		return fail(cmd, StateNotAuthorized, "the extension is not set up for this cluster; approve it first")
	}

	decision, err := d.Prompter.Confirm(ctx, enableDialog(c, d.backgroundParagraph()))
	switch {
	case errors.Is(err, approve.ErrNoApprovalChannel), errors.Is(err, approve.ErrUnsupportedPlatform):
		return fail(cmd, StateApprovalUnavailable, "no approval dialog is available on this system")
	case err != nil:
		return fail(cmd, StateError, "the approval dialog could not be shown")
	}
	switch decision {
	case approve.Approved:
	case approve.TimedOut:
		return fail(cmd, StateTimedOut, "no answer was given, so nothing was changed")
	default:
		return fail(cmd, StateDeclined, "you declined, so nothing was changed")
	}

	if err := d.Agent.Install(ctx); err != nil {
		r := fail(cmd, StateError, "the background renewal could not be set up on this computer")
		r.Background = string(agent.Off)
		return r
	}
	return Result{OK: true, Command: cmd, State: StateConnected, Context: c.Name, Background: string(agent.On), Message: "the credential will now be renewed in the background"}
}

// BackgroundStatus only reads, so the extension can show whether the job is on without asking anyone.
func BackgroundStatus(ctx context.Context, d Deps) Result {
	const cmd = "agent-status"
	if d.Agent == nil {
		return Result{OK: true, Command: cmd, State: StateIdle, Background: string(agent.Unsupported)}
	}
	st, err := d.Agent.Status(ctx)
	if err != nil {
		return fail(cmd, StateError, "the background renewal job could not be checked")
	}
	return Result{OK: true, Command: cmd, State: StateIdle, Background: string(st)}
}

// DisableBackground removes the job without a dialog: it only reduces what the helper does.
func DisableBackground(ctx context.Context, d Deps) Result {
	const cmd = "agent-disable"
	if !d.agentSupported() {
		return Result{OK: true, Command: cmd, State: StateIdle, Background: string(agent.Unsupported)}
	}
	if err := d.Agent.Uninstall(ctx); err != nil {
		return fail(cmd, StateError, "the background renewal job could not be removed")
	}
	return Result{OK: true, Command: cmd, State: StateIdle, Background: string(agent.Off), Message: "the background renewal job was removed"}
}
