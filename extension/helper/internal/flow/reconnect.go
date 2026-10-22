package flow

import "context"

// Reconnect restores the connection to the cluster the person approved earlier, when it was lost. It is for
// a backend that holds no credential (a reinstalled or cleaned extension, a failed install) and never asks
// anything. Every rule of Renew applies, and three more:
//
//   - it only runs for the one cluster the backend remembers as approved, so it cannot be used to connect a
//     cluster nobody approved;
//   - it never runs after the person disconnected or removed access: that choice is kept by the backend
//     across page reloads, restarts and the background job, and only a new approval (authorize) clears it;
//   - the cluster behind the context must have the same API address and certificate authority as when it was
//     approved, and the extension's access in it must still be exactly what was approved. If anything
//     differs it stops and says so. It never creates, changes or deletes anything in the cluster: it asks for
//     a token (a TokenRequest) and nothing else.
func Reconnect(ctx context.Context, d Deps) Result {
	const cmd = "reconnect"
	release, busy := d.acquire(cmd)
	if busy != nil {
		return *busy
	}
	defer release()

	backend, st, err := d.pickBackend(ctx)
	if err != nil {
		return backendFailure(cmd, err)
	}
	switch {
	case st.Connected:
		return Result{OK: true, Command: cmd, State: StateIdle, Context: st.Context, Message: "already connected; renewal keeps it current"}
	case st.Approved == nil:
		return Result{OK: true, Command: cmd, State: StateIdle, Message: "no cluster has been approved yet, so there is nothing to reconnect; connect one first"}
	case st.Approved.DisconnectedByUser:
		return Result{OK: true, Command: cmd, State: StateIdle, Context: st.Approved.Context, Message: "you disconnected this cluster, so it is not reconnected automatically; connect it again to approve it"}
	}

	c, refusal := d.supported(cmd, st.Approved.Context)
	if refusal != nil {
		return *refusal
	}
	cs, cluster, err := d.Clusters.Client(c.Name)
	if err != nil {
		return fail(cmd, StateError, err.Error())
	}
	if !sameIdentity(st.Approved, identityOf(c, cluster)) {
		return identityChanged(cmd)
	}
	if _, refusal := verified(ctx, cmd, c.Name, cs, cluster); refusal != nil {
		return *refusal
	}
	if refusal := requireApprovedAccess(ctx, cmd, cs); refusal != nil {
		return *refusal
	}
	return issueAndInstall(ctx, d, cmd, OriginReconnect, c, cs, cluster, backend)
}
