package flow

import (
	"context"

	"resilience-engine/helper/internal/rbac"
)

// Renew issues a fresh token for access that was ALREADY approved. It has three hard rules:
//
//   - it never shows a dialog and never writes to the cluster (no RBAC create, update or delete);
//   - it never recreates revoked access: if anything is missing or changed it stops and says so;
//   - it only runs for a cluster the backend already holds a managed credential for, so it cannot be
//     used to set up a cluster that was never approved.
//
// The extension UI calls it when the backend reports the token is due (or expired), which is also how
// it recovers after Docker Desktop restarts, extension updates and long gaps with the app closed.
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
	if !st.Managed || st.Context != c.Name {
		return fail(cmd, StateNotAuthorized, "the extension is not set up for this cluster; approve it first")
	}

	cs, cluster, err := d.Clusters.Client(c.Name)
	if err != nil {
		return fail(cmd, StateError, err.Error())
	}
	if _, err := cs.Discovery().ServerVersion(); err != nil {
		return fail(cmd, StateClusterUnreachable, "the cluster is not reachable; is Docker Desktop running and the kind cluster started?")
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
			return fail(cmd, StateDrifted, "the extension's access in the cluster was changed from what you approved, so no token was issued; review it and approve again")
		default: // absent or partial
			return fail(cmd, StateAuthorizationGone, "the extension's access was removed from the cluster, so no token was issued; approve it again to restore it")
		}
	}
	return issueAndInstall(ctx, d, cmd, c.Name, cs, cluster, backend)
}
