package flow

import (
	"context"

	"resilience-engine/helper/internal/clusters"
	"resilience-engine/helper/internal/rbac"
	"resilience-engine/helper/internal/transport"
)

// Renew issues a fresh token for access that was ALREADY approved. It has three hard rules:
//
//   - it never shows a dialog and never writes to the cluster (no RBAC create, update or delete);
//   - it never recreates revoked access: if anything is missing or changed it stops and says so;
//   - it only runs for a cluster the backend already holds a managed credential for, so it cannot be
//     used to set up a cluster that was never approved.
//
// The extension UI calls it when the backend reports the token is due (or expired), and the
// background job calls RenewManaged, so a long gap with the app closed, a sleeping laptop or a
// Docker Desktop restart all recover without anybody opening the extension.
func Renew(ctx context.Context, d Deps, name string) Result {
	const cmd = "renew"
	c, refusal := d.supported(cmd, name)
	if refusal != nil {
		return *refusal
	}

	backend, st, err := d.pickBackend(ctx)
	if err != nil {
		return backendFailure(cmd, err)
	}
	return renewWith(ctx, d, cmd, c, backend, st)
}

// RenewManaged is renewal with nobody asking: it reads what the backend holds and renews only a
// credential the helper itself installed, and only once that credential is due or has expired. It is
// what the background job runs, so every rule of Renew applies unchanged (no dialog, no cluster
// writes, never recreating revoked access), and a credential added by hand is never touched.
func RenewManaged(ctx context.Context, d Deps) Result {
	const cmd = "renew-managed"
	backend, st, err := d.pickBackend(ctx)
	if err != nil {
		return backendFailure(cmd, err)
	}
	if !st.Connected || !st.Managed {
		return Result{OK: true, Command: cmd, State: StateIdle, Message: "there is no automatically managed credential to renew"}
	}
	if st.State != "renew-soon" && st.State != "expired" {
		return Result{OK: true, Command: cmd, State: StateCurrent, Context: st.Context, ExpiresAt: st.ExpiresAt, RenewAt: st.RenewAt, Message: "the credential is not due for renewal"}
	}
	c, refusal := d.supported(cmd, st.Context)
	if refusal != nil {
		return *refusal
	}
	return renewWith(ctx, d, cmd, c, backend, st)
}

// renewWith is the part shared by Renew and RenewManaged, once the backend and its status are known.
func renewWith(ctx context.Context, d Deps, cmd string, c clusters.Context, backend transport.Backend, st transport.Status) Result {
	if !st.Managed || st.Context != c.Name {
		return fail(cmd, StateNotAuthorized, "the extension is not set up for this cluster; approve it first")
	}

	cs, cluster, err := d.Clusters.Client(c.Name)
	if err != nil {
		return fail(cmd, StateError, err.Error())
	}
	if _, refusal := verified(ctx, cmd, c.Name, cs, cluster); refusal != nil {
		return *refusal
	}

	report, err := rbac.Inspect(ctx, cs)
	if err != nil {
		return clusterError(cmd, err)
	}
	if !report.Usable() {
		switch report.Overall() {
		case rbac.OverallOverGranted:
			return overGranted(cmd, report)
		case rbac.OverallDrifted:
			if report.NeedsMoreAccess() {
				return fail(cmd, StateDrifted, "this version of the extension needs more read access than you approved before, so no token was issued; approve again to add it")
			}
			return fail(cmd, StateDrifted, "the extension's access in the cluster was changed from what you approved, so no token was issued; review it and approve again")
		default: // absent or partial
			return fail(cmd, StateAuthorizationGone, "the extension's access was removed from the cluster, so no token was issued; approve it again to restore it")
		}
	}
	return issueAndInstall(ctx, d, cmd, c.Name, cs, cluster, backend)
}
