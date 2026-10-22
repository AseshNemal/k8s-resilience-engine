package flow

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/kubeconfig"
	"resilience-engine/helper/internal/rbac"
	"resilience-engine/helper/internal/token"
	"resilience-engine/helper/internal/transport"
)

// Authorize is the one-time setup. Order matters, and each step can stop the flow without having
// changed anything:
//
//  1. the context must be a candidate local cluster that the cluster itself confirms (verify.go);
//  2. a backend must be reachable (so the user is never asked to approve something that cannot finish);
//  3. the current RBAC is inspected, and an over-granted service account stops the flow;
//  4. the user approves in a native dialog showing the exact cluster, permissions and changes;
//  5. only then is RBAC created or repaired, a token issued, and the credential delivered.
func Authorize(ctx context.Context, d Deps, name string) Result {
	const cmd = "authorize"
	c, refusal := d.supported(cmd, name)
	if refusal != nil {
		return *refusal
	}
	cs, cluster, err := d.Clusters.Client(c.Name)
	if err != nil {
		return fail(cmd, StateError, err.Error())
	}
	verdict, refusal := verified(ctx, cmd, c.Name, cs, cluster)
	if refusal != nil {
		return *refusal
	}

	backend, _, err := d.pickBackend(ctx)
	if err != nil {
		return backendFailure(cmd, err)
	}

	report, err := rbac.Inspect(ctx, cs)
	if err != nil {
		return clusterError(cmd, err)
	}
	if len(report.ExtraGrants) > 0 {
		return overGranted(cmd, report)
	}
	plan := rbac.Plan(report)

	decision, err := d.Prompter.Confirm(ctx, authorizeDialog(c, verdict.Version, verdict.Nodes, plan, d.backgroundNote()))
	switch {
	case errors.Is(err, approve.ErrNoApprovalChannel), errors.Is(err, approve.ErrUnsupportedPlatform):
		return fail(cmd, StateApprovalUnavailable, "no approval dialog is available on this system; use the manual connection instead")
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

	if err := rbac.Apply(ctx, cs, report); err != nil {
		return clusterError(cmd, err)
	}
	r := issueAndInstall(ctx, d, cmd, c.Name, cs, cluster, backend)
	r.Changes = describe(plan)
	if r.OK {
		// The person already approved the job in the dialog above, so this is its only consent.
		r = d.startBackground(ctx, r)
	}
	return r
}

// issueAndInstall issues a fresh token and hands it to the backend. It is shared by authorize and
// renew, and is the only place a token exists. It never prints or returns it.
func issueAndInstall(ctx context.Context, d Deps, cmd, name string, cs kubeClient, cluster kubeconfig.Cluster, backend transport.Backend) Result {
	issued, err := token.Issue(ctx, cs, token.RequestedLifetime, d.now())
	switch {
	case errors.Is(err, token.ErrServiceAccountMissing):
		return fail(cmd, StateAuthorizationGone, "the extension's access was removed from the cluster; approve it again")
	case errors.Is(err, token.ErrForbidden):
		return fail(cmd, StateRBACDenied, "your kubeconfig is not allowed to issue tokens for the extension's service account")
	case err != nil:
		return fail(cmd, StateError, err.Error())
	}
	doc, _, err := kubeconfig.Build(cluster, issued.Token)
	if err != nil {
		return fail(cmd, StateError, err.Error())
	}
	st, err := backend.Install(ctx, transport.InstallRequest{Kubeconfig: doc, Context: name, ExpiresAt: issued.ExpiresAt})
	if err != nil {
		return backendFailure(cmd, err)
	}
	return Result{
		OK: true, Command: cmd, State: StateConnected, Context: name, Transport: backend.Name(),
		ExpiresAt: iso(issued.ExpiresAt), RenewAt: st.RenewAt,
		Message: fmt.Sprintf("connected; the cluster granted a %s token", issued.Lifetime().Round(minute)),
	}
}

func clusterError(cmd string, err error) Result {
	switch {
	case errors.Is(err, rbac.ErrForbidden):
		return fail(cmd, StateRBACDenied, "your kubeconfig is not allowed to manage access on this cluster; ask a cluster administrator")
	case errors.Is(err, rbac.ErrOverGranted):
		return fail(cmd, StateOverGranted, "another role binding grants the extension's service account extra access")
	default:
		return fail(cmd, StateError, "the cluster refused the request")
	}
}

func overGranted(cmd string, report rbac.Report) Result {
	var names []string
	for _, g := range report.ExtraGrants {
		n := g.Kind + " " + g.Name
		if g.Namespace != "" {
			n += " in " + g.Namespace
		}
		names = append(names, approve.Sanitize(n, 80))
	}
	r := fail(cmd, StateOverGranted, "another binding grants the extension's service account extra access, so nothing was changed: "+strings.Join(names, "; "))
	return r
}
