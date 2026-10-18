package flow

import (
	"context"
	"errors"
	"strings"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/rbac"
)

// Revoke removes the extension's access from the cluster, after a native confirmation that lists
// exactly what will be deleted, and then disconnects the backend. Deleting the service account
// invalidates every token already issued.
func Revoke(ctx context.Context, d Deps, name string) Result {
	const cmd = "revoke"
	c, refusal := d.supported(cmd, name)
	if refusal != nil {
		return *refusal
	}
	cs, _, err := d.Clusters.Client(c.Name)
	if err != nil {
		return fail(cmd, StateError, err.Error())
	}
	if _, err := cs.Discovery().ServerVersion(); err != nil {
		return fail(cmd, StateClusterUnreachable, "the cluster is not reachable")
	}
	report, err := rbac.Inspect(ctx, cs)
	if err != nil {
		return clusterError(cmd, err)
	}
	plan := rbac.PlanRevoke(ctx, cs, report)
	dels := plan.Delete

	if len(dels) > 0 {
		decision, err := d.Prompter.Confirm(ctx, revokeDialog(c, plan))
		switch {
		case errors.Is(err, approve.ErrNoApprovalChannel), errors.Is(err, approve.ErrUnsupportedPlatform):
			return fail(cmd, StateApprovalUnavailable, "no confirmation dialog is available on this system; remove the access with kubectl instead")
		case err != nil:
			return fail(cmd, StateError, "the confirmation dialog could not be shown")
		}
		switch decision {
		case approve.Approved:
		case approve.TimedOut:
			return fail(cmd, StateTimedOut, "no answer was given, so nothing was removed")
		default:
			return fail(cmd, StateDeclined, "you declined, so nothing was removed")
		}
		if err := rbac.Revoke(ctx, cs, plan); err != nil {
			return clusterError(cmd, err)
		}
	}

	// Disconnect the backend whether or not the cluster side had anything left to remove.
	r := Result{OK: true, Command: cmd, State: StateDisconnected, Context: c.Name, Changes: describe(dels)}
	kept := ""
	if len(plan.Keep) > 0 {
		kept = " Kept: " + strings.Join(plan.Keep, "; ") + "."
	}
	backend, _, err := d.pickBackend(ctx)
	if err != nil {
		r.Message = "access was removed from the cluster, but the extension backend is not reachable; disconnect it from the extension." + kept
		return r
	}
	if err := backend.Disconnect(ctx); err != nil {
		r.Message = "access was removed from the cluster, but the backend could not be disconnected; disconnect it from the extension." + kept
		return r
	}
	r.Transport = backend.Name()
	r.Message = "access was removed from the cluster and the extension was disconnected." + kept
	return r
}
