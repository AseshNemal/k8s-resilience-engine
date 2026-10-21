package flow

import "context"

// Status reports the backend's connection state. It contains no credential.
func Status(ctx context.Context, d Deps) Result {
	backend, st, err := d.pickBackend(ctx)
	if err != nil {
		return backendFailure("status", err)
	}
	state := StateNotConnected
	if st.Connected {
		state = StateConnected
	}
	return Result{OK: true, Command: "status", State: state, Transport: backend.Name(), Backend: &st, Context: st.Context}
}
